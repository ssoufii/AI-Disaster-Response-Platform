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

Rerouting a terminal failure onto the household's next channel is deliberately
*not* here yet — it is triggered from the status webhook (issue #12). Until then
a failed attempt stops at a failed row.
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
from app.logging_config import redact_phone
from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import Channel, DeliveryStatus
from app.models.household import Household

logger = structlog.get_logger(__name__)

# Where Twilio posts every delivery state change. Absolute, because Twilio
# resolves it from the public internet, not from inside this process.
STATUS_CALLBACK_PATH = "/webhooks/twilio/status"

# A household's preferred channel is attempt 1; each fallback increments from
# there (issue #12).
FIRST_ATTEMPT = 1


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

    if household.preferred_channel != Channel.SMS.value:
        log.info("delivery.channel_not_implemented", channel=household.preferred_channel)
        return False

    if content is None:
        # Generation runs before delivery, so this means the household joined
        # the zone between the two phases. Re-dispatching picks it up.
        log.warning("delivery.no_content", channel=household.preferred_channel)
        return False

    await _send_sms(session, alert, household, content)
    return True


async def _send_sms(
    session: AsyncSession, alert: Alert, household: Household, content: AlertContent
) -> DeliveryAttempt:
    """Record the attempt, then send it. Never raises."""
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))

    attempt = DeliveryAttempt(
        alert_id=alert.id,
        household_id=household.id,
        channel=Channel.SMS.value,
        attempt_number=FIRST_ATTEMPT,
        status=DeliveryStatus.QUEUED.value,
    )
    session.add(attempt)
    # Committed before the send: the row is the record that we tried.
    await session.commit()

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
