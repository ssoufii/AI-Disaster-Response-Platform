"""A stand-in for Twilio, for the live simulation demo.

In ``SIMULATION_MODE`` this is what ``delivery_service.get_client()`` returns
instead of the Twilio SDK client, so nothing is ever texted or called and the
demo costs nothing to run.

It imitates Twilio from the outside and nothing more: a send answers with a SID,
and a little later the delivery statuses arrive as signed POSTs to the same
webhooks Twilio would call. Everything after that — signature validation,
idempotency, fallback rerouting, marking a household unreached, the WebSocket
events — is the real code, unchanged. The simulator never touches the database
and never decides what a status *means*.

Like Twilio, it knows only what the send told it: the callback URL comes from
the ``status_callback`` argument and the keypress URL from the ``<Gather>`` in
the call's TwiML.

``confirmed_received`` stays honest (Domain Rule 6): it is only ever produced by
a simulated keypress posted to the real confirmation webhook, standing in for
the person on the line.
"""

import asyncio
import logging
import random
import re
import uuid
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from twilio.request_validator import RequestValidator

from app.config import settings

logger = structlog.get_logger(__name__)

# httpx announces every request at INFO. Each simulated callback is one, and the
# webhook already logs what it did with it, so the announcement is only noise.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Twilio's magic test numbers all start with this, and each is documented as a
# number that cannot be reached. The simulator fails every send to one, on every
# channel, so the seeded "guaranteed-fail" households always walk their whole
# fallback chain.
UNREACHABLE_PREFIX = "+1500555"

WHATSAPP_SCHEME = "whatsapp:"

# The digit the voice script asks for (see delivery_service.CONFIRMATION_DIGIT).
CONFIRM_DIGIT = "1"

_GATHER_ACTION = re.compile(r'<Gather[^>]*\baction="([^"]+)"')


class SimulatedTwilio:
    """The slice of ``twilio.rest.Client`` that ``delivery_service`` uses."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.messages = _Messages(self)
        self.calls = _Calls(self)
        self._tasks: set[asyncio.Task[None]] = set()

    def active_count(self) -> int:
        """How many simulated sends are still reporting statuses."""
        return len(self._tasks)

    async def drain(self) -> None:
        """Wait until every simulated send has reported its last status.

        A reroute started by one send's failure creates another send, so this
        loops until the set is genuinely empty.
        """
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def cancel(self) -> None:
        """Stop every in-flight simulation (shutdown, or the nightly reset)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _start(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _pause(self) -> None:
        await asyncio.sleep(
            self.rng.uniform(settings.SIM_STEP_MIN_SECONDS, settings.SIM_STEP_MAX_SECONDS)
        )

    def _unreachable(self, to: str) -> bool:
        return to.removeprefix(WHATSAPP_SCHEME).startswith(UNREACHABLE_PREFIX)

    async def _run_message(self, sid: str, params: dict[str, Any]) -> None:
        """A message's life: sent, then delivered or undelivered."""
        to = str(params["to"])
        rate = (
            settings.SIM_CLIP_FAILURE_RATE
            if params.get("media_url")
            else settings.SIM_SMS_FAILURE_RATE
        )
        fails = self._unreachable(to) or self.rng.random() < rate
        url = str(params["status_callback"])
        base = {"MessageSid": sid, "To": to, "From": str(params["from_"])}

        await self._pause()
        await _post(url, {**base, "MessageStatus": "sent"})
        await self._pause()
        if fails:
            await _post(
                url,
                {
                    **base,
                    "MessageStatus": "undelivered",
                    "ErrorCode": "30003",
                    "ErrorMessage": "Unreachable destination handset (simulated)",
                },
            )
        else:
            await _post(url, {**base, "MessageStatus": "delivered"})

    async def _run_call(self, sid: str, params: dict[str, Any]) -> None:
        """A call's life: ringing, then unanswered — or answered, maybe confirmed."""
        to = str(params["to"])
        url = str(params["status_callback"])
        base = {"CallSid": sid, "To": to, "From": str(params["from_"])}
        unanswered = self._unreachable(to) or self.rng.random() < settings.SIM_VOICE_NO_ANSWER_RATE

        await self._pause()
        await _post(url, {**base, "CallStatus": "ringing"})
        await self._pause()
        if unanswered:
            outcome = self.rng.choice(["no-answer", "no-answer", "busy"])
            await _post(url, {**base, "CallStatus": outcome})
            return

        await _post(url, {**base, "CallStatus": "in-progress"})
        await self._pause()
        gather = _GATHER_ACTION.search(str(params.get("twiml", "")))
        if gather and self.rng.random() < settings.SIM_VOICE_CONFIRM_RATE:
            # The person on the line pressing 1. The real webhook decides what
            # that means; this only reports the keypress.
            await _post(gather.group(1), {**base, "Digits": CONFIRM_DIGIT})
        await _post(url, {**base, "CallStatus": "completed"})


class _Messages:
    def __init__(self, sim: SimulatedTwilio) -> None:
        self._sim = sim

    async def create_async(self, **params: Any) -> SimpleNamespace:
        sid = f"SM{uuid.uuid4().hex}"
        self._sim._start(self._sim._run_message(sid, params))
        return SimpleNamespace(sid=sid)


class _Calls:
    def __init__(self, sim: SimulatedTwilio) -> None:
        self._sim = sim

    async def create_async(self, **params: Any) -> SimpleNamespace:
        sid = f"CA{uuid.uuid4().hex}"
        self._sim._start(self._sim._run_call(sid, params))
        return SimpleNamespace(sid=sid)


async def _post(url: str, params: dict[str, str]) -> None:
    """Deliver one signed callback to this app's own webhook, in process.

    Signed exactly as Twilio signs — over the full callback URL and the form
    fields — with the simulation-only token from ``config``, so the webhook's
    signature check runs for real and would refuse anything else.

    The request goes straight into the ASGI app rather than over the network:
    the host's public URL may not be reachable from inside its own container,
    and the webhook does not care which way the bytes arrived.
    """
    from app.main import app  # imported late: main imports the routers that import us

    signature = RequestValidator(settings.TWILIO_AUTH_TOKEN).compute_signature(url, params)
    path = urlsplit(url).path
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://simulator"
        ) as client:
            response = await client.post(
                path, data=params, headers={"X-Twilio-Signature": signature}
            )
        if response.status_code >= 400:
            logger.error(
                "twilio_simulator.callback_refused", path=path, status=response.status_code
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # One lost callback must not end the simulation of every other send.
        logger.error("twilio_simulator.callback_failed", path=path, error_type=type(exc).__name__)


_simulator: SimulatedTwilio | None = None


def get_simulator() -> SimulatedTwilio:
    """The process-wide simulator, built on first use."""
    global _simulator
    if _simulator is None:
        _simulator = SimulatedTwilio()
    return _simulator
