"""Twilio delivery.

The only place in the codebase where the Twilio SDK is instantiated (CLAUDE.md,
Backend Conventions). Routes hand in an alert and its households; they never see
the SDK.

Three things shape this module:

1. **Every attempt is a row, written before the send.** The ``DeliveryAttempt``
   is committed as ``queued`` *before* Twilio is called, so a send that raises —
   or a process that dies mid-call — still leaves evidence that this household
   was tried (CLAUDE.md, Domain Rule 2). A row exists because we tried, not
   because Twilio answered.
2. **Every send carries a ``statusCallback``.** Delivery state arrives by
   webhook, never by polling, so a send without a callback URL is a send whose
   outcome is unknowable.
3. **One household's failure is one failed row.** A Twilio error is recorded on
   that household's attempt and the dispatch continues down the zone; it is
   never allowed to abort the other households' sends.
4. **Every state a row reaches is broadcast.** A console open when a dispatch
   starts learns about the new attempt here rather than waiting for Twilio's
   first callback — and a send Twilio refuses outright never gets a callback at
   all, so without this its failure would never reach the console.
5. **A failure reroutes onto the household's next channel.** ``reroute_failed_attempt``
   is the feature this project is named for: a terminal failure creates a *new*
   attempt on the next channel in ``fallback_channel_order`` rather than
   mutating the failed one. It is called as a task from the status webhook the
   instant Twilio reports the failure — never from a polling loop (Domain
   Rule 3).
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import lru_cache

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.http.async_http_client import AsyncTwilioHttpClient
from twilio.rest import Client

from app.config import settings
from app.db import session_scope
from app.logging_config import redact_phone
from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import Channel, DeliveryStatus
from app.models.household import Household
from app.services import content_generator, dispatcher_ws

logger = structlog.get_logger(__name__)

# Where Twilio posts every delivery state change. Absolute, because Twilio
# resolves it from the public internet, not from inside this process.
STATUS_CALLBACK_PATH = "/webhooks/twilio/status"

# A household's preferred channel is attempt 1; each fallback increments from
# there.
FIRST_ATTEMPT = 1

# The statuses that end an attempt in failure and so start a reroute. Twilio's
# `undelivered` and `failed` both map to FAILED; `no-answer` and `busy` arrive
# with the voice channel (#16). In-flight statuses — queued, sending, ringing —
# are deliberately absent: a fallback fired on one of those would race the
# delivery it is giving up on (CLAUDE.md, Twilio Integration).
FALLBACK_TRIGGER_STATUSES = frozenset({DeliveryStatus.FAILED, DeliveryStatus.NO_ANSWER})

# Channels this build can actually send on. Voice (#16) and ASL/video (#19) are
# still ahead in CLAUDE.md's Build Order.
SENDABLE_CHANNELS = frozenset({Channel.SMS})


@lru_cache
def get_client() -> Client:
    """The process-wide Twilio client.

    Async-backed, because a zone dispatch sends from inside the request path and
    CLAUDE.md allows no blocking I/O there. Built lazily so importing this module
    (in tests, in Alembic) never requires credentials — and so the async HTTP
    client is constructed inside a running event loop, which is the only place it
    can be.
    """
    return Client(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
        http_client=AsyncTwilioHttpClient(),
    )


def status_callback_url() -> str:
    """The absolute URL Twilio posts delivery status to."""
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{STATUS_CALLBACK_PATH}"


async def deliver_alert(
    session: AsyncSession, alert: Alert, households: Sequence[Household]
) -> int:
    """Send an alert to every household in its zone. Returns attempts started.

    Households that already have an attempt for this alert are skipped, so
    re-dispatching never sends a household the same warning twice — the same
    guarantee content generation already makes about its rows.

    Only SMS is wired up so far; voice (#16) and ASL/video (#19) are still
    ahead in CLAUDE.md's Build Order. A household on one of those channels is
    logged and left without an attempt rather than sent something it cannot
    receive. That is a build-order gap, not Domain Rule 4's ``unreached``, which
    means an exhausted fallback chain and is issue #13's to set.
    """
    log = logger.bind(alert_id=str(alert.id))

    contents = {
        content.household_id: content
        for content in (
            await session.exec(select(AlertContent).where(AlertContent.alert_id == alert.id))
        ).all()
    }
    already_attempted = set(
        (
            await session.exec(
                select(DeliveryAttempt.household_id).where(DeliveryAttempt.alert_id == alert.id)
            )
        ).all()
    )

    started = 0
    for household in households:
        if household.id in already_attempted:
            continue
        if await _deliver_to_household(session, alert, household, contents.get(household.id)):
            started += 1

    log.info("delivery.dispatch_complete", households=len(households), attempts_started=started)
    return started


async def _deliver_to_household(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    content: AlertContent | None,
) -> bool:
    """Send to one household. Returns whether an attempt was started."""
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))

    if Channel(household.preferred_channel) not in SENDABLE_CHANNELS:
        log.info("delivery.channel_not_implemented", channel=household.preferred_channel)
        return False

    if content is None:
        # Generation runs before delivery, so this means the household joined
        # the zone between the two phases. Re-dispatching picks it up.
        log.warning("delivery.no_content", channel=household.preferred_channel)
        return False

    attempt = await _start_attempt(
        session, alert, household, Channel(household.preferred_channel), FIRST_ATTEMPT
    )
    await _send(session, alert, household, attempt, content)
    return True


async def _start_attempt(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    channel: Channel,
    attempt_number: int,
    rerouted_from: DeliveryAttempt | None = None,
) -> DeliveryAttempt:
    """Write the row that says this household was tried on this channel.

    Committed before anything is sent: the row is the record that we tried, not
    a record of what Twilio answered (Domain Rule 2). A fallback calls this with
    the next channel, an incremented number and the attempt it is replacing,
    which is why nothing here updates an existing row — there is no path on
    which an attempt is reused.
    """
    attempt = DeliveryAttempt(
        alert_id=alert.id,
        household_id=household.id,
        channel=channel.value,
        attempt_number=attempt_number,
        status=DeliveryStatus.QUEUED.value,
    )
    session.add(attempt)
    await session.commit()

    if rerouted_from is not None:
        # The reroute is announced on the row that failed — one event saying
        # both what went wrong and where it is going next — and before the new
        # attempt's own event, so the console reads the two in the order they
        # happened.
        await dispatcher_ws.broadcast_delivery_update(
            rerouted_from, fallback_triggered=True, fallback_channel=channel
        )
    # The console shows the household as in flight from here, not from Twilio's
    # first callback — a grid that lags the dispatch it is watching is exactly
    # the staleness this console exists to avoid.
    await dispatcher_ws.broadcast_delivery_update(attempt)
    return attempt


async def _send(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    attempt: DeliveryAttempt,
    content: AlertContent,
) -> None:
    """Send a recorded attempt on its own channel.

    Only SMS is wired up; a row on a channel that is not (a fallback onto voice,
    say) stays ``queued`` and is logged. It is deliberately not marked failed:
    nothing was tried, so calling it a failure would walk the household down its
    fallback chain for a channel this build simply has not built yet.
    """
    if Channel(attempt.channel) is Channel.SMS:
        await _send_sms(session, alert, household, attempt, content)
        return

    logger.bind(alert_id=str(alert.id), household_id=str(household.id)).warning(
        "delivery.channel_not_implemented",
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
    )


async def _send_sms(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    attempt: DeliveryAttempt,
    content: AlertContent,
) -> DeliveryAttempt:
    """Hand one recorded attempt to Twilio. Never raises."""
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))

    try:
        message = await get_client().messages.create_async(
            to=household.phone_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=content.generated_text,
            status_callback=status_callback_url(),
        )
    except Exception as exc:
        # Deliberately broad: an auth error, an unroutable number and a timeout
        # all mean the same thing to this household — this channel did not take
        # the message — and all of them belong on the row rather than raising
        # into the dispatch loop and stranding the households after it.
        _mark_failed(attempt, f"{type(exc).__name__}: {exc}")
        session.add(attempt)
        await session.commit()
        log.warning(
            "delivery.send_failed",
            channel=Channel.SMS.value,
            attempt_number=attempt.attempt_number,
            to=redact_phone(household.phone_number),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # Twilio never accepted this message, so it will never call back about
        # it. This broadcast is the only way the failure reaches the console.
        await dispatcher_ws.broadcast_delivery_update(attempt)
        return attempt

    attempt.twilio_sid = message.sid
    session.add(attempt)
    await session.commit()
    log.info(
        "delivery.sent",
        channel=Channel.SMS.value,
        attempt_number=attempt.attempt_number,
        to=redact_phone(household.phone_number),
        twilio_sid=message.sid,
    )
    return attempt


async def reroute_failed_attempt(attempt_id: uuid.UUID) -> None:
    """Reroute one failed attempt onto the household's next fallback channel.

    The entry point the status webhook hands to its background task, which is
    why it takes an id rather than a row and opens its own session: by the time
    it runs, the webhook has answered Twilio and closed the session the attempt
    was read on. It is started the instant Twilio reports the failure — there is
    no polling loop or cron anywhere in this path (Domain Rule 3).

    Never raises. Nothing is waiting on it: an exception here would be swallowed
    by the event loop, so it is logged instead, at error level, because a
    household whose reroute failed is a household that may now go unreached.
    """
    async with session_scope() as session:
        attempt = await session.get(DeliveryAttempt, attempt_id)
        if attempt is None:
            logger.error("delivery.reroute_attempt_missing", attempt_id=str(attempt_id))
            return
        try:
            await reroute(session, attempt)
        except Exception as exc:
            logger.error(
                "delivery.reroute_failed",
                alert_id=str(attempt.alert_id),
                household_id=str(attempt.household_id),
                attempt_id=str(attempt.id),
                channel=attempt.channel,
                attempt_number=attempt.attempt_number,
                error_type=type(exc).__name__,
                error=str(exc),
            )


async def reroute(session: AsyncSession, failed: DeliveryAttempt) -> DeliveryAttempt | None:
    """Start the next attempt in a household's fallback chain.

    Returns the new attempt, or ``None`` when the chain has no channel left —
    the point at which Domain Rule 4 says the household is ``unreached`` and
    needs a human. Marking it so, and emitting the event that flags it, is
    issue #13's; here the chain simply ends, loudly, in the log.

    The failed attempt is never touched. Its status, its ``error_reason`` and
    its timestamps are the evidence that this channel was tried, and the audit
    trail the console draws is exactly that row plus this new one (Domain
    Rule 2).
    """
    log = logger.bind(alert_id=str(failed.alert_id), household_id=str(failed.household_id))

    household = await session.get(Household, failed.household_id)
    alert = await session.get(Alert, failed.alert_id)
    if household is None or alert is None:
        log.error("delivery.reroute_row_missing", attempt_id=str(failed.id))
        return None

    next_channel = next_fallback_channel(household, failed.attempt_number)
    if next_channel is None:
        log.warning(
            "delivery.fallback_exhausted",
            channel=failed.channel,
            attempt_number=failed.attempt_number,
        )
        return None

    content = await _content_for_channel(session, alert, household, next_channel)
    attempt = await _start_attempt(
        session, alert, household, next_channel, failed.attempt_number + 1, rerouted_from=failed
    )
    log.info(
        "delivery.fallback_triggered",
        failed_channel=failed.channel,
        failed_attempt_number=failed.attempt_number,
        fallback_channel=next_channel.value,
        attempt_number=attempt.attempt_number,
    )
    await _send(session, alert, household, attempt, content)
    return attempt


def next_fallback_channel(household: Household, failed_attempt_number: int) -> Channel | None:
    """The channel to try after ``failed_attempt_number`` failed, if any.

    ``fallback_channel_order`` holds only the fallbacks — the preferred channel
    is attempt 1 and is not in the list — so attempt *n*'s successor is the
    list's *n*-th entry, counting from one: attempt 1 (sms) falls back to
    ``fallback_channel_order[0]`` (voice), whose failure as attempt 2 falls back
    to ``fallback_channel_order[1]``. ``None`` means the chain is exhausted.
    """
    order = household.fallback_channel_order
    index = failed_attempt_number - 1
    if index < 0 or index >= len(order):
        return None
    return Channel(order[index])


async def _content_for_channel(
    session: AsyncSession, alert: Alert, household: Household, channel: Channel
) -> AlertContent:
    """This household's content for one channel, generating it if it has none.

    A household's first attempt has content because the dispatch generated it
    for the preferred channel. A fallback lands on a channel nobody has written
    for yet, so it is generated here, before the send — an SMS script read aloud
    down a phone line is not what the voice channel should say.

    ``content_generator.generate`` does not raise: a Claude failure returns the
    pre-written template, so it can never be the reason a reroute stops.
    """
    existing = (
        await session.exec(
            select(AlertContent).where(
                AlertContent.alert_id == alert.id,
                AlertContent.household_id == household.id,
                AlertContent.channel == channel.value,
            )
        )
    ).first()
    if existing is not None:
        return existing

    generated = await content_generator.generate(alert, household, channel.value)
    content = AlertContent(
        alert_id=alert.id,
        household_id=household.id,
        channel=channel.value,
        generated_text=generated.sms_text,
        generated_script=generated.voice_script,
        video_caption_text=generated.asl_video_caption,
        language=generated.language_used,
    )
    session.add(content)
    await session.commit()
    logger.info(
        "delivery.fallback_content_generated",
        alert_id=str(alert.id),
        household_id=str(household.id),
        channel=channel.value,
        language=content.language,
    )
    return content


def _mark_failed(attempt: DeliveryAttempt, reason: str) -> None:
    attempt.status = DeliveryStatus.FAILED.value
    attempt.completed_at = datetime.now(UTC)
    attempt.error_reason = reason


async def latest_attempts_by_household(
    session: AsyncSession, alert_id: uuid.UUID
) -> dict[uuid.UUID, DeliveryAttempt]:
    """The most recent attempt per household for one alert.

    "Most recent" is the highest ``attempt_number``: attempts are ordered by the
    fallback chain that produced them, not by wall-clock time. The earlier rows
    stay exactly where they are — this only picks which one the snapshot shows.
    """
    attempts = (
        await session.exec(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.alert_id == alert_id)
            .order_by(DeliveryAttempt.attempt_number)
        )
    ).all()
    return {attempt.household_id: attempt for attempt in attempts}
