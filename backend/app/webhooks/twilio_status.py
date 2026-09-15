"""Twilio delivery status callbacks.

Twilio posts here on every delivery state change; this is the only way the
system learns whether a warning actually landed. Polling Twilio for status is
never an option — the console's liveness comes from these callbacks.

The handler does the least work that can be done: look the attempt up by the SID
Twilio quotes, apply the status, return 200. Nothing slow belongs here — no
Claude generation, no outbound send — because Twilio times the callback out and
retries, and a slow handler turns one delivery into a pile of duplicates.

Twilio reaches this endpoint from the public internet, so it cannot be
authenticated the way the rest of the API is. What stands in its place is the
signature check below: every callback must carry an ``X-Twilio-Signature`` that
only the account's auth token can produce, or it is refused with a 403 before a
single row is read. Without it, anyone who found the URL could post forged
delivery state into the audit trail — or, once #12 lands, trigger rerouting
against real households.

Idempotency under Twilio's retries (#9) is still missing.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qsl

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.request_validator import RequestValidator

from app.config import settings
from app.db import get_session
from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import DeliveryStatus
from app.schemas.delivery_attempt import TwilioStatusAck
from app.services.delivery_service import status_callback_url

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

logger = structlog.get_logger(__name__)

# Twilio signs each callback with the account auth token and quotes the result
# here.
SIGNATURE_HEADER = "X-Twilio-Signature"

# Twilio's message statuses, mapped onto ours. Voice adds `ringing`,
# `in-progress`, `completed`, `no-answer` and `busy`; those arrive with the
# voice channel (#16) and are deliberately absent rather than guessed at here.
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
    """The callback's form params, once its signature proves Twilio sent them.

    A dependency rather than a check inside the handler, so the refusal happens
    before the handler runs and there is no path on which a forged callback
    touches the database — the endpoint fails closed, it does not
    process-then-reject.

    The signature covers the URL Twilio posted to, and the URL checked here is
    ``PUBLIC_BASE_URL`` — the same one ``delivery_service`` hands Twilio as the
    ``statusCallback``, so the two can never drift. It is also the only URL that
    can be right in deployment: ngrok (and any proxy) terminates TLS and rewrites
    the host, so ``request.url`` is the internal address, not the one Twilio
    signed.
    """
    params = await _form_params(request)
    signature = request.headers.get(SIGNATURE_HEADER, "")
    url = status_callback_url()

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
    params: dict[str, str] = Depends(verified_twilio_params),
    session: AsyncSession = Depends(get_session),
) -> TwilioStatusAck:
    """Apply one Twilio status callback to its DeliveryAttempt."""
    # `SmsSid`/`SmsStatus` are Twilio's older aliases; both are still sent.
    twilio_sid = params.get("MessageSid") or params.get("SmsSid")
    raw_status = params.get("MessageStatus") or params.get("SmsStatus")

    if not twilio_sid or not raw_status:
        logger.warning("twilio_status.malformed_callback", fields=sorted(params))
        return TwilioStatusAck(result="ignored", reason="missing sid or status")

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

    status = MESSAGE_STATUS_MAP.get(raw_status)
    if status is None:
        log.warning(
            "twilio_status.unmapped_status",
            twilio_sid=twilio_sid,
            status=raw_status,
            channel=attempt.channel,
        )
        return TwilioStatusAck(result="ignored", reason=f"unmapped status {raw_status}")

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
    return TwilioStatusAck(result="applied", status=status)


def _error_reason(params: dict[str, str]) -> str | None:
    """Twilio's own explanation of a failure, kept verbatim on the row."""
    code = params.get("ErrorCode")
    message = params.get("ErrorMessage")
    if code and message:
        return f"{code}: {message}"
    return code or message or None
