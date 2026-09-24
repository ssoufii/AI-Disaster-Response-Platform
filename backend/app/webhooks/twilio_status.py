"""Twilio delivery callbacks: what happened to a send, and what a person did.

Twilio posts here on every delivery state change; this is the only way the
system learns whether a warning actually landed. Polling Twilio for status is
never an option — the console's liveness comes from these callbacks.

Two endpoints, and the difference between them is Domain Rule 6. The status
callback reports what became of a send: a message the carrier took, a call that
rang out. The confirmation callback carries a digit a household pressed during
that call, which is a person saying they are safe — the only thing in this
system that writes ``confirmed_received``, and never something a delivery status
is allowed to imply.

Messages and calls both report here. What a message says (``MessageSid``,
``delivered``) and what a call says (``CallSid``, ``completed``) differ only in
wording, and that difference stops at ``_identify_callback``; everything after it
is the same work whatever was sent.

The handler does the least work that can be done: look the attempt up by the SID
Twilio quotes, apply the status, return 200. Nothing slow belongs here — no
Claude generation, no outbound send — because Twilio times the callback out and
retries, and a slow handler turns one delivery into a pile of duplicates.

A terminal failure is the one thing that does start further work, and it starts
it *after* the 200: the reroute onto the household's next channel is scheduled
as a background task and generates and sends on its own time. That task is the
only trigger rerouting has — nothing polls Twilio or sweeps the table for failed
attempts (CLAUDE.md, Domain Rule 3).

Twilio reaches both endpoints from the public internet, so neither can be
authenticated the way the rest of the API is. What stands in their place is the
signature check below: every callback must carry an ``X-Twilio-Signature`` that
only the account's auth token can produce, or it is refused with a 403 before a
single row is read. Without it, anyone who found the URL could post forged
delivery state into the audit trail — or a forged keypress marking a household
safe that nobody has heard from.

The handler is also idempotent, because Twilio retries any callback it does not
get a timely 200 for. Each state Twilio reports is recorded as a
``DeliveryStatusCallback`` keyed on ``(twilio_sid, status)``; a callback whose
state is already on record is a no-op that still answers 200, so a retry cannot
write the same status twice or reroute the same failure twice.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qsl

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.request_validator import RequestValidator

from app.config import settings
from app.db import get_session
from app.models.delivery_attempt import DeliveryAttempt
from app.models.delivery_status_callback import DeliveryStatusCallback
from app.models.enums import DeliveryStatus
from app.schemas.delivery_attempt import TwilioStatusAck
from app.services import delivery_service, dispatcher_ws
from app.services.delivery_service import (
    CONFIRMATION_DIGIT,
    FALLBACK_TRIGGER_STATUSES,
    gather_callback_url,
    status_callback_url,
)

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

logger = structlog.get_logger(__name__)

# Twilio signs each callback with the account auth token and quotes the result
# here.
SIGNATURE_HEADER = "X-Twilio-Signature"

# Twilio's message statuses, mapped onto ours.
#
# `sent` is Twilio's "handed to the carrier" — still in flight toward
# `delivered`, so it is not terminal and is not a receipt.
MESSAGE_STATUS_MAP = {
    "accepted": DeliveryStatus.QUEUED,
    "queued": DeliveryStatus.QUEUED,
    "scheduled": DeliveryStatus.QUEUED,
    "sending": DeliveryStatus.SENDING,
    "sent": DeliveryStatus.SENDING,
    "delivered": DeliveryStatus.DELIVERED,
    "undelivered": DeliveryStatus.FAILED,
    "failed": DeliveryStatus.FAILED,
}

# Twilio's call statuses, mapped onto the same ones. A call progresses
# `queued` → `initiated` → `ringing` → `in-progress` → an outcome, so everything
# before the outcome is this system's "in flight".
#
# `completed` is the call having run its course — Twilio delivered the warning
# to whoever or whatever picked up. It is `delivered`, never
# `confirmed_received`: a call that was answered and heard out is still not a
# person saying they are safe, and only a keypress can say that (#17, Domain
# Rule 6).
#
# `busy` and `no-answer` are both the household not taking the call, which is
# what `NO_ANSWER` means and why they share it; Twilio's own word for each is
# kept verbatim on the `DeliveryStatusCallback` row. `canceled` is a call
# abandoned before it connected, which is a failure of this attempt like any
# other. Both outcomes are terminal failures, so both reroute (#12).
CALL_STATUS_MAP = {
    "queued": DeliveryStatus.QUEUED,
    "initiated": DeliveryStatus.SENDING,
    "ringing": DeliveryStatus.SENDING,
    "in-progress": DeliveryStatus.SENDING,
    "completed": DeliveryStatus.DELIVERED,
    "busy": DeliveryStatus.NO_ANSWER,
    "no-answer": DeliveryStatus.NO_ANSWER,
    "canceled": DeliveryStatus.FAILED,
    "failed": DeliveryStatus.FAILED,
}

# Statuses after which nothing more is coming for this attempt, so its
# `completed_at` is real rather than provisional.
TERMINAL_STATUSES = frozenset(
    {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED, DeliveryStatus.NO_ANSWER}
)


async def _form_params(request: Request) -> dict[str, str]:
    """Twilio's form-encoded body as a plain dict.

    Parsed from the raw body rather than via ``request.form()`` so the endpoint
    needs no multipart dependency: Twilio posts
    ``application/x-www-form-urlencoded``, which the standard library reads
    directly. Signature validation checks exactly this dict, which is why the
    handler receives its params from the validating dependency rather than
    parsing the body a second time.
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    return dict(parse_qsl(body, keep_blank_values=True))


async def verified_twilio_params(request: Request) -> dict[str, str]:
    """The status callback's params, once its signature proves Twilio sent them."""
    return await _verified_params(request, status_callback_url())


async def verified_gather_params(request: Request) -> dict[str, str]:
    """The confirmation callback's params, verified against its own URL.

    A separate dependency only because the signature covers the URL Twilio
    posted to, and this endpoint's URL is not the status callback's. Everything
    it refuses, and the reasons it refuses them, are the status callback's.
    """
    return await _verified_params(request, gather_callback_url())


async def _verified_params(request: Request, url: str) -> dict[str, str]:
    """One callback's form params, once its signature proves Twilio sent them.

    Reached through a dependency rather than called inside a handler, so the
    refusal happens before the handler runs and there is no path on which a
    forged callback touches the database — the endpoints fail closed, they do
    not process-then-reject.

    The signature covers the URL Twilio posted to, and the URL checked here is
    built from ``PUBLIC_BASE_URL`` — the same one ``delivery_service`` hands
    Twilio as the ``statusCallback`` or the gather's ``action``, so the two can
    never drift. It is also the only URL that can be right in deployment: ngrok
    (and any proxy) terminates TLS and rewrites the host, so ``request.url`` is
    the internal address, not the one Twilio signed.
    """
    params = await _form_params(request)
    signature = request.headers.get(SIGNATURE_HEADER, "")

    if not settings.TWILIO_AUTH_TOKEN:
        # Nothing to validate against means nothing can be trusted. Refusing is
        # the only safe reading: the alternative is an open endpoint on whichever
        # deployment forgot the variable.
        logger.error("twilio_status.signature_unverifiable", url=url)
        raise HTTPException(status_code=403, detail="Twilio signature cannot be verified")

    if not RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params, signature):
        # The presented signature and the token are both left out of the log: one
        # is a secret, the other is noise. What an operator needs is that a
        # forgery arrived, and against which URL.
        logger.warning(
            "twilio_status.invalid_signature",
            url=url,
            signature_present=bool(signature),
            fields=sorted(params),
        )
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    return params


@router.post("/status", response_model=TwilioStatusAck)
async def twilio_status(
    background: BackgroundTasks,
    params: dict[str, str] = Depends(verified_twilio_params),
    session: AsyncSession = Depends(get_session),
) -> TwilioStatusAck:
    """Apply one Twilio status callback to its DeliveryAttempt."""
    identified = _identify_callback(params)
    if identified is None:
        logger.warning("twilio_status.malformed_callback", fields=sorted(params))
        return TwilioStatusAck(result="ignored", reason="missing sid or status")
    twilio_sid, raw_status, status_map = identified

    attempt = (
        await session.exec(select(DeliveryAttempt).where(DeliveryAttempt.twilio_sid == twilio_sid))
    ).first()
    if attempt is None:
        # Not ours — someone else's message, or a callback that overtook the
        # commit of its own SID. 200 either way: a 4xx only makes Twilio retry
        # a callback we will never be able to place.
        logger.warning("twilio_status.unknown_sid", twilio_sid=twilio_sid, status=raw_status)
        return TwilioStatusAck(result="ignored", reason="unknown twilio_sid")

    log = logger.bind(alert_id=str(attempt.alert_id), household_id=str(attempt.household_id))

    status = status_map.get(raw_status)
    if status is None:
        log.warning(
            "twilio_status.unmapped_status",
            twilio_sid=twilio_sid,
            status=raw_status,
            channel=attempt.channel,
        )
        return TwilioStatusAck(result="ignored", reason=f"unmapped status {raw_status}")

    if attempt.status == DeliveryStatus.CONFIRMED_RECEIVED.value and (
        status is not DeliveryStatus.CONFIRMED_RECEIVED
    ):
        # The household pressed 1 mid-call, and the call's own ending is now
        # arriving behind it — the ordinary sequence, not an edge case, since
        # Twilio reports `completed` only once the call is over. Applying it
        # would write `delivered` over a receipt and quietly undo the one fact
        # in this system that came from a person (Domain Rule 6). Nothing else
        # is applied either: a confirmed attempt is finished, so a terminal
        # failure reported after it does not reroute a household that has
        # already said it is safe.
        log.info(
            "twilio_status.after_confirmation",
            twilio_sid=twilio_sid,
            channel=attempt.channel,
            attempt_number=attempt.attempt_number,
            status=status.value,
        )
        return TwilioStatusAck(
            result="ignored", status=status, reason="attempt already confirmed_received"
        )

    if not await _record_callback(session, attempt, twilio_sid, status, raw_status):
        # Already applied. Returning before anything is written is what makes
        # the retry harmless: no second status write, no second WebSocket event,
        # and no second fallback attempt row, because none of that is
        # downstream of this line.
        log.info(
            "twilio_status.duplicate_callback",
            twilio_sid=twilio_sid,
            channel=attempt.channel,
            attempt_number=attempt.attempt_number,
            status=status.value,
        )
        return TwilioStatusAck(result="duplicate", status=status)

    attempt.status = status.value
    if status in TERMINAL_STATUSES:
        attempt.completed_at = datetime.now(UTC)
    if status is DeliveryStatus.FAILED:
        attempt.error_reason = _error_reason(params)

    session.add(attempt)
    await session.commit()

    log.info(
        "twilio_status.applied",
        twilio_sid=twilio_sid,
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
    )
    # After the commit, so the consoles are never shown a state the database has
    # not accepted. In-process and non-blocking, so it does not slow the 200
    # down — nothing here waits on Twilio, Claude or the network.
    await dispatcher_ws.broadcast_delivery_update(attempt)

    if status in FALLBACK_TRIGGER_STATUSES:
        # Scheduled, not awaited: the reroute generates content and sends, and
        # Twilio times this callback out and retries if it is made to wait for
        # either. The task runs the moment the 200 is on the wire — this is the
        # whole of Domain Rule 3's "webhook-driven, not polled", and it is
        # downstream of the duplicate guard above, so a retried callback cannot
        # schedule a second reroute for the same failure.
        background.add_task(delivery_service.reroute_failed_attempt, attempt.id)
        log.info(
            "twilio_status.fallback_scheduled",
            twilio_sid=twilio_sid,
            channel=attempt.channel,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
        )

    return TwilioStatusAck(result="applied", status=status)


def _identify_callback(
    params: dict[str, str],
) -> tuple[str, str, dict[str, DeliveryStatus]] | None:
    """Which send this callback is about, and which vocabulary it speaks.

    One endpoint serves both channels, because everything after this line — the
    lookup by SID, the idempotency guard, the broadcast, the reroute — is the
    same work whatever was sent. What differs is only the field names Twilio
    uses and the status words it puts in them, and that difference stops here.

    A message and a call never share a callback, so the two are told apart by
    which fields arrived. Messages are checked first, and `SmsSid`/`SmsStatus`
    are Twilio's older aliases for them; both are still sent. ``None`` means
    neither pair was present.
    """
    message_sid = params.get("MessageSid") or params.get("SmsSid")
    message_status = params.get("MessageStatus") or params.get("SmsStatus")
    if message_sid and message_status:
        return message_sid, message_status, MESSAGE_STATUS_MAP

    call_sid = params.get("CallSid")
    call_status = params.get("CallStatus")
    if call_sid and call_status:
        return call_sid, call_status, CALL_STATUS_MAP

    return None


@router.post("/voice-confirmation", response_class=Response)
async def voice_confirmation(
    params: dict[str, str] = Depends(verified_gather_params),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Record the digit a household pressed during its call.

    Twilio posts here from the ``<Gather>`` wrapped around the call's script,
    quoting the call's ``CallSid`` and the ``Digits`` pressed. A ``1`` is the
    household saying it is safe, and it is the only input in this system that
    writes ``confirmed_received`` (Domain Rule 6). Every other case — a
    different digit, an empty gather, a SID that is not ours — leaves the
    attempt exactly where the call's own status put it, because none of them is
    a person answering.

    The answer is always TwiML and always a 200: Twilio plays what it is given
    back, and retries anything else. A confirmation is a status update on the
    attempt that placed the call, never a new attempt (Domain Rule 2), and it is
    not a failure, so nothing here reroutes.
    """
    twiml = Response(
        content=delivery_service.confirmation_ack_twiml(), media_type="application/xml"
    )

    call_sid = params.get("CallSid")
    if not call_sid:
        logger.warning("twilio_confirmation.malformed_callback", fields=sorted(params))
        return twiml

    attempt = (
        await session.exec(select(DeliveryAttempt).where(DeliveryAttempt.twilio_sid == call_sid))
    ).first()
    if attempt is None:
        logger.warning("twilio_confirmation.unknown_sid", twilio_sid=call_sid)
        return twiml

    log = logger.bind(alert_id=str(attempt.alert_id), household_id=str(attempt.household_id))

    digits = params.get("Digits", "")
    if digits != CONFIRMATION_DIGIT:
        # A mis-key, or a gather that ended with nothing in it. The call is
        # still whatever its status callback says it is — `delivered` at most —
        # and inferring a receipt from a household reaching for its keypad and
        # missing is exactly what Domain Rule 6 forbids.
        log.info(
            "twilio_confirmation.not_a_confirmation",
            twilio_sid=call_sid,
            attempt_number=attempt.attempt_number,
            digits=digits,
        )
        return twiml

    if not await _record_callback(
        session, attempt, call_sid, DeliveryStatus.CONFIRMED_RECEIVED, digits
    ):
        # Twilio retries this callback like any other, and a household can press
        # 1 twice. Either way the confirmation is one event, guarded by the same
        # `(twilio_sid, status)` record the status webhook uses — so no second
        # write and no second event to the console.
        log.info(
            "twilio_confirmation.duplicate_callback",
            twilio_sid=call_sid,
            attempt_number=attempt.attempt_number,
        )
        return twiml

    attempt.status = DeliveryStatus.CONFIRMED_RECEIVED.value
    # Terminal: a household that has answered is not waiting on anything
    # further, whatever the call does next.
    attempt.completed_at = datetime.now(UTC)
    session.add(attempt)
    await session.commit()

    log.info(
        "twilio_confirmation.applied",
        twilio_sid=call_sid,
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
    )
    # After the commit, as with every other broadcast: a console is never shown
    # a household as safe before the database has accepted it. This event is how
    # a dispatcher watching the grid sees the household drop off the list of
    # people who still need someone to go and knock.
    await dispatcher_ws.broadcast_delivery_update(attempt)
    return twiml


async def _record_callback(
    session: AsyncSession,
    attempt: DeliveryAttempt,
    twilio_sid: str,
    status: DeliveryStatus,
    raw_status: str,
) -> bool:
    """Claim this ``(twilio_sid, status)`` state, or report it already claimed.

    Returns ``True`` when the caller should go on to apply the update, ``False``
    when Twilio has already told us this state and the callback is a retry.

    The row is only flushed here, not committed: it lands in the same
    transaction as the status change it authorizes, so the record of "this state
    was applied" and the application of it can never disagree.

    Both halves of the check are needed. The lookup is what makes the ordinary
    case — a retry minutes later — a clean no-op. The unique constraint is what
    makes it correct when two retries arrive together, since the lookup alone is
    a check-then-write that both requests would pass.
    """
    already_applied = (
        await session.exec(
            select(DeliveryStatusCallback).where(
                DeliveryStatusCallback.twilio_sid == twilio_sid,
                DeliveryStatusCallback.status == status.value,
            )
        )
    ).first()
    if already_applied is not None:
        return False

    session.add(
        DeliveryStatusCallback(
            delivery_attempt_id=attempt.id,
            twilio_sid=twilio_sid,
            status=status.value,
            raw_status=raw_status,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        # The concurrent retry that got there first. Its transaction applies the
        # status; ours drops everything it staged and answers 200.
        await session.rollback()
        return False
    return True


def _error_reason(params: dict[str, str]) -> str | None:
    """Twilio's own explanation of a failure, kept verbatim on the row."""
    code = params.get("ErrorCode")
    message = params.get("ErrorMessage")
    if code and message:
        return f"{code}: {message}"
    return code or message or None
