"""No log line carries a full phone number, whoever wrote it.

CLAUDE.md's rule is "last 4 digits only", and the interesting failures are not
the call site that forgot. They are:

- ``error=str(exc)``, where a provider is quoting the number back at us. No audit
  of call sites catches that one, because the number is not in the source.
- The ``event`` message itself, which is just another key on the event dict.
- A number nested inside a dict or list of bound context.

So the rule is enforced by a processor over every event from every module, and
these are tests of that processor plus an audit of what the delivery path
actually emits.
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.logging_config import configure_logging, redact_phone, redact_phone_numbers
from app.models.enums import Channel
from app.services import content_generator, delivery_service
from tests.test_delivery import (
    RecordingLogger,
    _attempts,
    _dispatch_one_asl_household,
    _dispatch_one_sms_household,
    _dispatch_one_voice_household,
)

HOUSEHOLD_NUMBER = "+15550100001"

ALERT_TEXT = "Evacuate now. Go to Lincoln High School, 400 Oak St."


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every dispatch here generates content; none of it reaches Anthropic."""

    async def create(**_kwargs: Any) -> SimpleNamespace:
        payload = json.dumps(
            {
                "sms_text": ALERT_TEXT,
                "voice_script": f"{ALERT_TEXT} Press 1 if you are safe.",
                "asl_video_caption": ALERT_TEXT,
                "language_used": "en",
            }
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)])

    monkeypatch.setattr(
        content_generator,
        "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )


def _redact(**fields: Any) -> dict[str, Any]:
    """Run one event dict through the processor, as structlog would."""
    return redact_phone_numbers(None, "info", fields)


def test_a_number_in_a_field_keeps_only_its_last_four_digits() -> None:
    assert _redact(to=HOUSEHOLD_NUMBER) == {"to": "***0001"}


def test_a_number_in_the_message_itself_is_redacted() -> None:
    """``event`` is just another key, and a message can carry a number too."""
    redacted = _redact(event=f"sending to {HOUSEHOLD_NUMBER}")

    assert redacted["event"] == "sending to ***0001"


def test_a_number_quoted_back_by_a_provider_is_redacted() -> None:
    """The leak an audit of call sites cannot catch: it is not in our source."""
    redacted = _redact(
        event="delivery.send_failed",
        error=f"HTTP 400: The 'To' number {HOUSEHOLD_NUMBER} is not a valid phone number",
    )

    assert HOUSEHOLD_NUMBER not in redacted["error"]
    assert "***0001" in redacted["error"]


def test_a_whatsapp_address_keeps_its_scheme_and_loses_its_number() -> None:
    """Which transport a send used is worth keeping; the number is not."""
    redacted = _redact(to=f"whatsapp:{HOUSEHOLD_NUMBER}")

    assert redacted["to"] == "whatsapp:***0001"


def test_numbers_nested_in_context_are_redacted() -> None:
    redacted = _redact(
        household={"name": "Baker household", "phone_number": HOUSEHOLD_NUMBER},
        recipients=[HOUSEHOLD_NUMBER, "+15550100002"],
    )

    assert redacted["household"]["phone_number"] == "***0001"
    assert redacted["recipients"] == ["***0001", "***0002"]


def test_every_number_in_one_string_is_redacted() -> None:
    redacted = _redact(event=f"reroute {HOUSEHOLD_NUMBER} -> +15550100002")

    assert redacted["event"] == "reroute ***0001 -> ***0002"


def test_a_twilio_sid_is_left_alone() -> None:
    """A SID is a long run of digits, and mangling it would cost the audit trail.

    This is why the pattern requires the E.164 ``+``: every number this system
    holds has one, and nothing else in a log line does.
    """
    sid = f"SM{'0' * 32}"
    redacted = _redact(twilio_sid=sid, attempt_id=str(uuid.uuid4()))

    assert redacted["twilio_sid"] == sid


def test_a_number_longer_than_e164_allows_is_still_redacted() -> None:
    """The malformed number someone typed is the one that must not reach a log.

    A pattern capped at E.164's 15 digits would match *nothing* in a longer run
    and pass the whole thing through intact, which is the opposite of what a
    redaction rule is for.
    """
    redacted = _redact(to="+" + "1" * 18)

    assert redacted["to"] == "***1111"


@pytest.mark.parametrize(
    "value",
    [
        "attempt_number=1",
        "2026-01-01T00:00:00Z",
        "400: unroutable",
        "queued",
        "",
    ],
)
def test_ordinary_fields_pass_through_untouched(value: str) -> None:
    assert _redact(field=value) == {"field": value}


def test_values_that_are_not_text_are_left_as_they_came() -> None:
    """Rendering is the renderer's job; this processor only redacts."""
    redacted = _redact(attempt_number=1, fallback_triggered=True, fallback_channel=None)

    assert redacted == {"attempt_number": 1, "fallback_triggered": True, "fallback_channel": None}


def test_redact_phone_is_still_what_a_call_site_reaches_for() -> None:
    assert redact_phone(HOUSEHOLD_NUMBER) == "***0001"


def test_the_processor_is_installed_ahead_of_the_renderer() -> None:
    """A processor after the renderer would run on a string, far too late."""
    configure_logging()
    processors = structlog.get_config()["processors"]

    assert redact_phone_numbers in processors
    assert processors.index(redact_phone_numbers) < len(processors) - 1
    assert isinstance(processors[-1], structlog.processors.JSONRenderer)


# --- The delivery path, audited (issue #20) ----------------------------------
#
# The rule is about the delivery path specifically, so it is worth asserting on
# what that path actually emits rather than only on the processor in isolation.
# Every channel built by now is covered: SMS (#7), voice (#16), ASL/video (#19).


def _leaks_from_call_sites(recorder: RecordingLogger) -> list[str]:
    """Fields the delivery path handed over with a full number still in them.

    What a call site wrote, before the processor ran. Empty is the standard: a
    call site that logs a recipient is expected to call ``redact_phone`` itself,
    and the processor is the backstop rather than the mechanism.
    """
    return [
        f"{event}.{key}={value}"
        for _level, event, fields in recorder.calls
        for key, value in fields.items()
        if HOUSEHOLD_NUMBER in str(value)
    ]


def _leaks_after_redaction(recorder: RecordingLogger) -> list[str]:
    """Anything that would still reach the log with a full number in it.

    Zero is not a standard here, it is the rule: this is what the log file
    actually ends up holding.
    """
    return [
        f"{event}.{key}={value}"
        for _level, event, fields in recorder.calls
        for key, value in _redact(**fields).items()
        if HOUSEHOLD_NUMBER in str(value)
    ]


@pytest.fixture
def delivery_log(monkeypatch: pytest.MonkeyPatch) -> RecordingLogger:
    """Capture the delivery path's log lines before the processor runs on them.

    Deliberately upstream of ``configure_logging``: the point is to see what the
    call sites hand over, so a call site that leaked a full number would show up
    here rather than be quietly cleaned up on the way out.
    """
    recorder = RecordingLogger()
    monkeypatch.setattr(delivery_service, "logger", recorder)
    return recorder


async def test_a_sent_sms_logs_no_full_number(
    client: AsyncClient, delivery_log: RecordingLogger
) -> None:
    await _dispatch_one_sms_household(client, HOUSEHOLD_NUMBER)

    assert _leaks_from_call_sites(delivery_log) == []
    assert _leaks_after_redaction(delivery_log) == []


async def test_a_refused_send_logs_no_full_number(
    client: AsyncClient, delivery_log: RecordingLogger, twilio: Any
) -> None:
    """The failure path, where the provider's own words carry the number.

    The call site redacts the recipient it knows about; what it cannot redact is
    the copy Twilio put in its error message, which is exactly what the processor
    is for. So the leak is expected before redaction and forbidden after it.
    """
    twilio.messages.error = RuntimeError(f"unroutable: {HOUSEHOLD_NUMBER}")

    await _dispatch_one_sms_household(client, HOUSEHOLD_NUMBER)

    assert [leak for leak in _leaks_from_call_sites(delivery_log) if ".error=" not in leak] == []
    assert _leaks_after_redaction(delivery_log) == []


async def test_a_placed_call_logs_no_full_number(
    client: AsyncClient, delivery_log: RecordingLogger
) -> None:
    await _dispatch_one_voice_household(client, HOUSEHOLD_NUMBER)

    assert _leaks_from_call_sites(delivery_log) == []
    assert _leaks_after_redaction(delivery_log) == []


async def test_a_sent_clip_logs_no_full_number(
    client: AsyncClient, delivery_log: RecordingLogger
) -> None:
    await _dispatch_one_asl_household(client, HOUSEHOLD_NUMBER)

    assert _leaks_from_call_sites(delivery_log) == []
    assert _leaks_after_redaction(delivery_log) == []


async def test_a_reroute_logs_no_full_number(
    client: AsyncClient, session: AsyncSession, delivery_log: RecordingLogger, twilio: Any
) -> None:
    """The fallback path logs the number it is rerouting onto, on a second channel."""
    twilio.messages.error = RuntimeError("carrier rejected")
    alert_id, _ = await _dispatch_one_sms_household(
        client, HOUSEHOLD_NUMBER, fallback_channel_order=[Channel.VOICE.value]
    )

    await delivery_service.reroute_failed_attempt((await _attempts(session, alert_id))[0].id)

    assert _leaks_from_call_sites(delivery_log) == []
    assert _leaks_after_redaction(delivery_log) == []
