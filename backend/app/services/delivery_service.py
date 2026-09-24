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
6. **A household that runs out of channels is flagged, never dropped.** When
   the chain has no next channel, the household is marked ``unreached`` and an
   event is pushed asking for a human. Domain Rule 4 calls failing silently
   here the worst possible outcome, and this is the only place that judgement
   is made.
7. **A channel changes how a send is made, not what happens around it.** SMS
   hands Twilio a body; voice hands it TwiML that reads the household's
   ``voice_script`` aloud. Both write their row first, both carry the same
   ``statusCallback``, and both report back through the same webhook — so the
   status handling, idempotency and rerouting built for SMS carry over to voice
   without a second code path.
8. **A call listens for the keypress its own script asks for.** Every voice
   script ends by asking the household to press 1, so the ``<Say>`` is wrapped
   in a ``<Gather>`` whose result posts to the confirmation webhook. That
   keypress is the only thing in the system that can write
   ``confirmed_received`` (Domain Rule 6); nothing here infers it from a call
   being answered or heard out.
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
from twilio.twiml.voice_response import VoiceResponse

from app.config import settings
from app.db import session_scope
from app.logging_config import redact_phone
from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import Channel, DeliveryStatus, HouseholdStatus
from app.models.household import Household
from app.services import content_generator, dispatcher_ws

logger = structlog.get_logger(__name__)

# Where Twilio posts every delivery state change. Absolute, because Twilio
# resolves it from the public internet, not from inside this process.
STATUS_CALLBACK_PATH = "/webhooks/twilio/status"

# Where Twilio posts the digit a household pressed during a call. A separate
# endpoint from the status callback because it carries a different fact: the
# status callback says what happened to the call, this says what a person did.
GATHER_CALLBACK_PATH = "/webhooks/twilio/voice-confirmation"

# A household's preferred channel is attempt 1; each fallback increments from
# there.
FIRST_ATTEMPT = 1

# The statuses that end an attempt in failure and so start a reroute. Twilio's
# `undelivered` and `failed` both map to FAILED; a call's `no-answer` and `busy`
# both map to NO_ANSWER. In-flight statuses — queued, sending, ringing — are
# deliberately absent: a fallback fired on one of those would race the delivery
# it is giving up on (CLAUDE.md, Twilio Integration).
FALLBACK_TRIGGER_STATUSES = frozenset({DeliveryStatus.FAILED, DeliveryStatus.NO_ANSWER})

# Channels this build can actually send on. ASL/video (#19) is still ahead in
# CLAUDE.md's Build Order.
SENDABLE_CHANNELS = frozenset({Channel.SMS, Channel.VOICE})

# A call reports only `completed` unless the events are asked for by name, and
# the console is meant to show a call ringing, not jump from queued to its
# outcome. `answered` arrives as CallStatus `in-progress`.
VOICE_STATUS_CALLBACK_EVENTS = ["initiated", "ringing", "answered", "completed"]

# The household's language as Twilio's `<Say>` wants it. A warning read aloud by
# a voice that cannot pronounce it is barely a warning at all, so the language is
# set explicitly rather than left at Twilio's en-US default.
#
# Only the languages this system generates content in are listed. An unlisted
# language leaves the attribute off — Twilio then reads it in its default
# voice, which is wrong but audible, and `delivery.voice_language_unsupported`
# says so in the log rather than letting the gap pass unnoticed.
VOICE_LANGUAGES = {
    "en": "en-US",
    "es": "es-MX",
    "vi": "vi-VN",
}

# The digit every generated script and every fallback template asks for, and the
# only one that means "I am safe". Anything else pressed is not a confirmation.
CONFIRMATION_DIGIT = "1"

# One digit, and seconds to press it. The gather is a single question, not a
# menu: there is nothing to key in beyond the answer the script asked for. The
# wait is longer than Twilio's five-second default because the household it is
# waiting on has just been told to evacuate.
CONFIRMATION_NUM_DIGITS = 1
CONFIRMATION_TIMEOUT_SECONDS = 10


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


def gather_callback_url() -> str:
    """The absolute URL Twilio posts a call's keypress to."""
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{GATHER_CALLBACK_PATH}"


async def deliver_alert(
    session: AsyncSession, alert: Alert, households: Sequence[Household]
) -> int:
    """Send an alert to every household in its zone. Returns attempts started.

    Households that already have an attempt for this alert are skipped, so
    re-dispatching never sends a household the same warning twice — the same
    guarantee content generation already makes about its rows.

    SMS and voice are wired up; ASL/video (#19) is still ahead in CLAUDE.md's
    Build Order. A household on that channel is logged and left without an
    attempt rather than sent something it cannot receive. That is a build-order
    gap, not Domain Rule 4's ``unreached``, which means an exhausted fallback
    chain and is issue #13's to set.
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

    SMS and voice are wired up; a row on a channel that is not (a fallback onto
    video, say) stays ``queued`` and is logged. It is deliberately not marked
    failed: nothing was tried, so calling it a failure would walk the household
    down its fallback chain for a channel this build simply has not built yet.
    """
    channel = Channel(attempt.channel)
    if channel is Channel.SMS:
        await _send_sms(session, alert, household, attempt, content)
        return
    if channel is Channel.VOICE:
        await _send_voice(session, alert, household, attempt, content)
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
    """Hand one recorded attempt to Twilio as a message. Never raises."""
    try:
        message = await get_client().messages.create_async(
            to=household.phone_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=content.generated_text,
            status_callback=status_callback_url(),
        )
    except Exception as exc:
        await _record_refused_send(session, alert, household, attempt, exc)
        return attempt

    await _record_accepted_send(session, alert, household, attempt, message.sid)
    return attempt


async def _send_voice(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    attempt: DeliveryAttempt,
    content: AlertContent,
) -> DeliveryAttempt:
    """Place the call that reads one recorded attempt aloud. Never raises.

    The TwiML is handed to Twilio inline rather than fetched from a URL of ours:
    the script is already written and sitting in the row, so an endpoint that
    served it back would be a second public surface to authenticate for no gain.
    (The ``<Gather>`` does need a callback URL for the keypress, which is a
    different thing — an answer coming *in*, not a script going out.)

    Call statuses are asked for by name, because Twilio otherwise reports only
    the outcome and a console watching a dispatch would show a household sitting
    at ``queued`` for the length of the ring.
    """
    try:
        call = await get_client().calls.create_async(
            to=household.phone_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            twiml=_voice_twiml(alert, household, content),
            status_callback=status_callback_url(),
            status_callback_event=VOICE_STATUS_CALLBACK_EVENTS,
            status_callback_method="POST",
        )
    except Exception as exc:
        await _record_refused_send(session, alert, household, attempt, exc)
        return attempt

    await _record_accepted_send(session, alert, household, attempt, call.sid)
    return attempt


def _voice_twiml(alert: Alert, household: Household, content: AlertContent) -> str:
    """The TwiML that reads this household's voice script down the line.

    ``generated_script`` and not ``generated_text``: the SMS text is written to
    be read with the eyes, and the voice script is the same facts written to be
    heard — which is why a fallback onto voice generates its own content rather
    than reusing what was sent by SMS.

    Nothing is composed here beyond the language: the script is Claude's output
    verbatim, and appending to it would be this module inventing words into an
    alert (Domain Rule 1).

    The ``<Say>`` sits *inside* a ``<Gather>`` because the script it reads ends
    by asking the household to press 1, and a household that already knows it is
    safe should not have to hear the rest of the warning out before it can
    answer. What comes back from that keypress is the only receipt this system
    recognises (#17, Domain Rule 6).

    Nothing follows the gather. A household that presses nothing simply reaches
    the end of the call, which Twilio reports as ``completed`` and this system
    records as ``delivered`` — silence is never walked upward into a
    confirmation, and with ``actionOnEmptyResult`` left off, it does not even
    reach the confirmation endpoint.
    """
    response = VoiceResponse()
    gather = response.gather(
        input="dtmf",
        num_digits=CONFIRMATION_NUM_DIGITS,
        timeout=CONFIRMATION_TIMEOUT_SECONDS,
        action=gather_callback_url(),
        method="POST",
    )
    language = _twilio_say_language(content.language)
    if language is None:
        logger.warning(
            "delivery.voice_language_unsupported",
            alert_id=str(alert.id),
            household_id=str(household.id),
            language=content.language,
        )
        gather.say(content.generated_script)
    else:
        gather.say(content.generated_script, language=language)
    return str(response)


def confirmation_ack_twiml() -> str:
    """What the confirmation endpoint answers Twilio with: nothing to say.

    Twilio expects TwiML back from a gather's ``action`` URL and plays whatever
    it is given. An empty response ends the call, which is the right end to one:
    the warning has been read and the household has answered it. Saying anything
    further would be this module composing words into a call whose content is
    Claude's (Domain Rule 1) — and in one language, into calls placed in three.
    """
    return str(VoiceResponse())


def _twilio_say_language(language: str) -> str | None:
    """``VOICE_LANGUAGES`` for a BCP-47 tag, or ``None`` if there is no voice.

    ``language_used`` is BCP-47, so it can carry a region Claude chose
    (``es-419``) that this map does not list. The primary subtag is what picks
    the voice, and falling back to it is the difference between a Spanish
    warning read in Spanish and one read in English.
    """
    return VOICE_LANGUAGES.get(language) or VOICE_LANGUAGES.get(language.split("-")[0].lower())


async def _record_refused_send(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    attempt: DeliveryAttempt,
    exc: Exception,
) -> None:
    """Record a send Twilio would not take, on the row and on the console.

    The caller catches deliberately broadly: an auth error, an unroutable number
    and a timeout all mean the same thing to this household — this channel did
    not take the warning — and all of them belong on the row rather than raising
    into the dispatch loop and stranding the households after it.
    """
    _mark_failed(attempt, f"{type(exc).__name__}: {exc}")
    session.add(attempt)
    await session.commit()
    logger.bind(alert_id=str(alert.id), household_id=str(household.id)).warning(
        "delivery.send_failed",
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
        to=redact_phone(household.phone_number),
        error_type=type(exc).__name__,
        error=str(exc),
    )
    # Twilio never accepted this send, so it will never call back about it. This
    # broadcast is the only way the failure reaches the console.
    await dispatcher_ws.broadcast_delivery_update(attempt)


async def _record_accepted_send(
    session: AsyncSession,
    alert: Alert,
    household: Household,
    attempt: DeliveryAttempt,
    twilio_sid: str,
) -> None:
    """Store the SID Twilio answered with — the handle every callback quotes.

    The attempt stays ``queued``: Twilio took it, which is not the same as the
    household receiving it, and certainly not the same as the household reading
    or hearing it (Domain Rule 6). What happens next arrives by webhook.
    """
    attempt.twilio_sid = twilio_sid
    session.add(attempt)
    await session.commit()
    logger.bind(alert_id=str(alert.id), household_id=str(household.id)).info(
        "delivery.sent",
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
        to=redact_phone(household.phone_number),
        twilio_sid=twilio_sid,
    )


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
    needs a human, which is what ``_mark_unreached`` below records and
    announces.

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
        await _mark_unreached(session, household, failed)
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


async def _mark_unreached(
    session: AsyncSession, household: Household, failed: DeliveryAttempt
) -> None:
    """Record that this household's every channel has been tried and failed.

    The end of the chain, and the worst outcome this system has: no channel is
    left to try, so the household stays unwarned unless a person goes to it.
    Domain Rule 4 is explicit that this must never be the quiet path — the
    status is written so a reload of ``GET /alerts/{id}/status`` still shows it,
    the event is emitted so an open console goes red without one, and the log
    line is a warning rather than an info.

    ``last_known_status`` lives on the household, not on the attempt, because
    "unreached" is a statement about the household after its whole chain ran
    out — which is exactly what the console colours red (#14).

    The write is committed before the broadcast, so no console is shown a
    household the database has not accepted as unreached.
    """
    household.last_known_status = HouseholdStatus.UNREACHED.value
    session.add(household)
    await session.commit()

    logger.warning(
        "delivery.fallback_exhausted",
        alert_id=str(failed.alert_id),
        household_id=str(household.id),
        channel=failed.channel,
        attempt_number=failed.attempt_number,
        last_known_status=household.last_known_status,
    )
    await dispatcher_ws.broadcast_household_unreached(household, failed)


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
