"""SMS dispatch, the Twilio status webhook, fallback rerouting, and the snapshot.

The end-to-end slice: dispatch → Twilio → status callback → DB → reroute onto
the next channel → snapshot. Twilio is replaced by the autouse ``twilio``
fixture in ``conftest`` and Anthropic by ``claude_calls`` below, so nothing here
reaches a live API (CLAUDE.md, Testing).
"""

import json
import pathlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.delivery_status_callback import DeliveryStatusCallback
from app.models.household import Household
from app.services import content_generator, delivery_service
from app.webhooks import twilio_status
from scripts.seed import TWILIO_UNDELIVERABLE_SMS_NUMBER, seed_demo_data
from tests.conftest import (
    PUBLIC_BASE_URL,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    FakeTwilio,
    twilio_signed_headers,
)

SMS_TEXT = "Evacuate now. Go to Lincoln High School, 400 Oak St."


@pytest.fixture(autouse=True)
def claude_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every dispatch here generates content; none of it reaches Anthropic.

    Returns the calls as they are made, so a test can check *what* was asked
    for — a reroute generates for the channel it is rerouting onto, not for the
    one that just failed.
    """
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        payload = json.dumps(
            {
                "sms_text": SMS_TEXT,
                "voice_script": f"{SMS_TEXT} Press 1 if you are safe.",
                "asl_video_caption": SMS_TEXT,
                "language_used": "en",
            }
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)])

    monkeypatch.setattr(
        content_generator,
        "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    return calls


def _requested_channels(calls: list[dict[str, Any]]) -> list[str]:
    """The channel each generation was asked for, in order."""
    return [json.loads(call["messages"][0]["content"])["channel"] for call in calls]


async def _zone(client: AsyncClient, name: str = "Riverside District") -> str:
    return (await client.post("/zones", json={"name": name})).json()["id"]


async def _alert(client: AsyncClient, zone_id: str) -> str:
    return (
        await client.post(
            "/alerts",
            json={
                "title": "Flash flood evacuation",
                "raw_message": "Evacuate the riverside area immediately.",
                "severity": "evacuate_now",
                "facts": {"shelter": "Lincoln High School, 400 Oak St"},
                "zone_id": zone_id,
            },
        )
    ).json()["id"]


async def _household(
    client: AsyncClient, zone_id: str, phone_number: str, **overrides: object
) -> str:
    payload: dict[str, object] = {
        "name": "Baker household",
        "phone_number": phone_number,
        "language": "en",
        "preferred_channel": "sms",
        "fallback_channel_order": ["voice"],
        "zone_id": zone_id,
    }
    return (await client.post("/households", json=payload | overrides)).json()["id"]


async def _dispatch_one_sms_household(
    client: AsyncClient, phone_number: str = "+15550100001", **overrides: object
) -> tuple[str, str]:
    """Draft, dispatch, and return ``(alert_id, household_id)``."""
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    household_id = await _household(client, zone_id, phone_number, **overrides)
    await client.post(f"/alerts/{alert_id}/dispatch")
    return alert_id, household_id


async def _post_status(client: AsyncClient, **params: str) -> Any:
    """POST a status callback exactly as Twilio does: form-encoded and signed."""
    return await client.post(
        "/webhooks/twilio/status", data=params, headers=twilio_signed_headers(params)
    )


class RecordingLogger:
    """Captures what the webhook logged, without a structlog round-trip.

    ``bind`` mirrors structlog's: the bound context comes back merged into every
    line the bound logger writes, which is how a test checks that a delivery-path
    line carries its ``alert_id`` and ``household_id``.
    """

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.context = context or {}

    def bind(self, **kwargs: Any) -> "RecordingLogger":
        bound = RecordingLogger(self.context | kwargs)
        bound.calls = self.calls  # one record, whether bound or not
        return bound

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.calls.append((level, event, self.context | kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)


async def _attempts(session: AsyncSession, alert_id: str) -> list[DeliveryAttempt]:
    return list(
        (
            await session.exec(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.alert_id == uuid.UUID(alert_id))
                .order_by(DeliveryAttempt.attempt_number)
            )
        ).all()
    )


# --- Dispatch: the send ------------------------------------------------------


async def test_dispatch_sends_sms_and_records_the_first_attempt(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, household_id = await _dispatch_one_sms_household(client)

    attempts = await _attempts(session, alert_id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.household_id == uuid.UUID(household_id)
    assert attempt.channel == "sms"
    assert attempt.attempt_number == 1
    # Queued, not delivered: the send only handed it to Twilio.
    assert attempt.status == "queued"
    assert attempt.twilio_sid == twilio.messages.sent[0].sid
    assert attempt.completed_at is None
    assert attempt.error_reason is None


async def test_dispatch_sends_the_generated_sms_text_with_a_status_callback(
    client: AsyncClient, twilio: FakeTwilio
) -> None:
    await _dispatch_one_sms_household(client, phone_number="+15550100077")

    assert len(twilio.messages.sent) == 1
    params = twilio.messages.sent[0].params
    assert params["to"] == "+15550100077"
    assert params["from_"] == TWILIO_PHONE_NUMBER
    # The household's own generated content, not the dispatcher's raw message.
    assert params["body"] == SMS_TEXT
    assert params["status_callback"] == f"{PUBLIC_BASE_URL}/webhooks/twilio/status"


async def test_dispatch_reports_how_many_deliveries_it_started(client: AsyncClient) -> None:
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    for i in range(3):
        await _household(client, zone_id, f"+1555010{i:04d}")

    body = (await client.post(f"/alerts/{alert_id}/dispatch")).json()

    assert body["households"] == 3
    assert body["content_generated"] == 3
    assert body["deliveries_started"] == 3


async def test_dispatching_twice_does_not_send_a_household_a_second_message(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)

    second = await client.post(f"/alerts/{alert_id}/dispatch")

    assert second.json()["deliveries_started"] == 0
    assert len(twilio.messages.sent) == 1
    assert len(await _attempts(session, alert_id)) == 1


async def test_a_channel_that_is_not_yet_built_is_not_attempted(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Voice (#16) and ASL/video (#19) are still ahead in the build order; a
    # household on one of them gets no attempt rather than an SMS it did not
    # ask for.
    alert_id, _ = await _dispatch_one_sms_household(client, preferred_channel="voice")

    assert twilio.messages.sent == []
    assert await _attempts(session, alert_id) == []


async def test_a_refused_send_still_leaves_a_failed_attempt_row(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Domain Rule 2: the row exists because we tried, not because Twilio agreed.
    twilio.messages.error = RuntimeError("unroutable number")

    alert_id, _ = await _dispatch_one_sms_household(client)

    attempts = await _attempts(session, alert_id)
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].twilio_sid is None
    assert attempts[0].completed_at is not None
    assert "unroutable number" in attempts[0].error_reason


async def test_one_households_failure_does_not_strand_the_rest_of_the_zone(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    await _household(client, zone_id, "+15550100201")
    await _household(client, zone_id, "+15550100202")

    # Fail only the first send, then let the rest through.
    original = twilio.messages.create_async
    calls = {"n": 0}

    async def create_async(**params: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("carrier rejected")
        return await original(**params)

    twilio.messages.create_async = create_async  # type: ignore[method-assign]

    body = (await client.post(f"/alerts/{alert_id}/dispatch")).json()

    assert body["deliveries_started"] == 2
    statuses = sorted(attempt.status for attempt in await _attempts(session, alert_id))
    assert statuses == ["failed", "queued"]


# --- The status webhook ------------------------------------------------------


async def test_delivered_callback_marks_the_attempt_delivered(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    response = await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert response.status_code == 200
    assert response.json()["result"] == "applied"
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "delivered"
    assert attempt.completed_at is not None


async def test_in_flight_callback_updates_status_without_completing_the_attempt(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="sent")

    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    # `sent` is "handed to the carrier" — still in flight, and never a receipt.
    assert attempt.status == "sending"
    assert attempt.completed_at is None


async def test_failure_callback_records_twilios_own_error_text(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _post_status(
        client,
        MessageSid=sid,
        MessageStatus="undelivered",
        ErrorCode="30006",
        ErrorMessage="Landline or unreachable carrier",
    )

    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "failed"
    assert attempt.completed_at is not None
    assert attempt.error_reason == "30006: Landline or unreachable carrier"


async def test_callback_accepts_twilios_legacy_sms_field_names(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, SmsSid=sid, SmsStatus="delivered")

    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "delivered"


async def test_callback_for_an_unknown_sid_is_ignored_but_still_returns_200(
    client: AsyncClient,
) -> None:
    # A 4xx would only make Twilio retry a callback we can never place.
    response = await _post_status(client, MessageSid="SMnotours", MessageStatus="delivered")

    assert response.status_code == 200
    assert response.json()["result"] == "ignored"


async def test_callback_with_an_unmapped_status_leaves_the_attempt_alone(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    response = await _post_status(client, MessageSid=sid, MessageStatus="in-progress")

    assert response.status_code == 200
    assert response.json()["result"] == "ignored"
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "queued"


async def test_callback_missing_its_fields_is_ignored(client: AsyncClient) -> None:
    response = await _post_status(client, AccountSid="AC123")

    assert response.status_code == 200
    assert response.json()["result"] == "ignored"


# --- Webhook idempotency -----------------------------------------------------
#
# Twilio retries a callback it does not get a timely 200 for. A retry must
# change nothing the first delivery of it already changed.


async def _callbacks(session: AsyncSession, sid: str) -> list[DeliveryStatusCallback]:
    return list(
        (
            await session.exec(
                select(DeliveryStatusCallback).where(DeliveryStatusCallback.twilio_sid == sid)
            )
        ).all()
    )


async def test_a_retried_callback_does_not_apply_the_status_a_second_time(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    first = await _post_status(client, MessageSid=sid, MessageStatus="delivered")
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    completed_at = attempt.completed_at

    second = await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert first.json()["result"] == "applied"
    # Still a 200: anything else and Twilio keeps retrying, compounding the
    # problem the guard exists to solve.
    assert second.status_code == 200
    assert second.json()["result"] == "duplicate"
    await session.refresh(attempt)
    assert attempt.status == "delivered"
    # The row was not written again — `completed_at` would have moved.
    assert attempt.completed_at == completed_at


async def test_a_retried_callback_creates_no_second_row_anywhere(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # The duplicate returns before any side effect, which is what keeps a retried
    # failure from producing a second WebSocket event (#10) or a second fallback
    # attempt, both of which hang off this handler.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    for _ in range(3):
        await _post_status(
            client, MessageSid=sid, MessageStatus="failed", ErrorCode="30006", ErrorMessage="Dead"
        )

    # Three callbacks, one failure: the original attempt and the single fallback
    # the first of them triggered.
    assert len(await _attempts(session, alert_id)) == 2
    assert len(await _callbacks(session, sid)) == 1


async def test_a_status_progression_for_one_sid_is_not_a_duplicate(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # sending → delivered is two real events about one message, not a retry. The
    # key is (sid, status), not the sid alone.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    sending = await _post_status(client, MessageSid=sid, MessageStatus="sending")
    delivered = await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert [sending.json()["result"], delivered.json()["result"]] == ["applied", "applied"]
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "delivered"
    assert attempt.completed_at is not None


async def test_a_status_that_arrives_again_after_a_later_one_does_not_regress_the_attempt(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Twilio's retry of the `sending` callback can land after `delivered` has
    # already been applied. Keying on what has been recorded — rather than on
    # the attempt's current status — is what stops it walking the row backwards.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid
    await _post_status(client, MessageSid=sid, MessageStatus="sending")
    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    late_retry = await _post_status(client, MessageSid=sid, MessageStatus="sending")

    assert late_retry.json()["result"] == "duplicate"
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "delivered"


async def test_twilios_two_names_for_one_failure_are_applied_once(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # `undelivered` and `failed` both mean the send failed, so the second is a
    # second telling of one state change — and, once #12 lands, would otherwise
    # be a second reroute. The recorded key is the mapped status for this reason.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid
    await _post_status(
        client,
        MessageSid=sid,
        MessageStatus="undelivered",
        ErrorCode="30006",
        ErrorMessage="Landline or unreachable carrier",
    )

    second = await _post_status(
        client, MessageSid=sid, MessageStatus="failed", ErrorCode="30008", ErrorMessage="Unknown"
    )

    assert second.json()["result"] == "duplicate"
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.error_reason == "30006: Landline or unreachable carrier"


async def test_the_same_status_for_a_different_message_is_not_a_duplicate(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Two households, one status: the guard keys on the SID too, or the second
    # household's delivery would be silently swallowed as a repeat of the first.
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    await _household(client, zone_id, "+15550100501")
    await _household(client, zone_id, "+15550100502")
    await client.post(f"/alerts/{alert_id}/dispatch")

    for message in twilio.messages.sent:
        response = await _post_status(client, MessageSid=message.sid, MessageStatus="delivered")
        assert response.json()["result"] == "applied"

    statuses = [attempt.status for attempt in await _attempts(session, alert_id)]
    assert statuses == ["delivered", "delivered"]


async def test_the_dedupe_key_is_enforced_by_the_database(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Two retries can arrive at the same moment, and the handler's lookup alone
    # is a check-then-write both would pass. The constraint is the guarantee.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid
    await _post_status(client, MessageSid=sid, MessageStatus="delivered")
    attempt = (await _attempts(session, alert_id))[0]

    session.add(
        DeliveryStatusCallback(
            delivery_attempt_id=attempt.id,
            twilio_sid=sid,
            status="delivered",
            raw_status="delivered",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_a_duplicate_callback_is_logged_against_its_alert_and_household(
    client: AsyncClient, twilio: FakeTwilio, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator watching a dispatch needs retries to be visible, and every
    # delivery-path line carries alert_id and household_id (CLAUDE.md, Logging).
    alert_id, household_id = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid
    await _post_status(client, MessageSid=sid, MessageStatus="delivered")
    recorder = RecordingLogger()
    monkeypatch.setattr(twilio_status, "logger", recorder)

    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert [(level, event) for level, event, _ in recorder.calls] == [
        ("info", "twilio_status.duplicate_callback")
    ]
    context = recorder.calls[0][2]
    assert context["alert_id"] == alert_id
    assert context["household_id"] == household_id
    assert context["status"] == "delivered"


# --- Webhook signature validation --------------------------------------------
#
# The valid-signature case is covered by every test above: `_post_status` signs
# each callback the way Twilio does, and they only pass because the endpoint
# accepts it. What follows is the refusal side.


async def _post_unsigned_status(client: AsyncClient, **params: str) -> Any:
    return await client.post("/webhooks/twilio/status", data=params)


async def _assert_attempt_untouched(session: AsyncSession, alert_id: str) -> None:
    """The forged callback claimed `delivered`; the row must still say queued."""
    attempt = (await _attempts(session, alert_id))[0]
    await session.refresh(attempt)
    assert attempt.status == "queued"
    assert attempt.completed_at is None


async def test_callback_without_a_signature_is_refused(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    response = await _post_unsigned_status(client, MessageSid=sid, MessageStatus="delivered")

    assert response.status_code == 403
    await _assert_attempt_untouched(session, alert_id)


async def test_callback_signed_with_the_wrong_token_is_refused(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)
    params = {"MessageSid": twilio.messages.sent[0].sid, "MessageStatus": "delivered"}

    response = await client.post(
        "/webhooks/twilio/status",
        data=params,
        headers=twilio_signed_headers(params, token="not-our-auth-token"),
    )

    assert response.status_code == 403
    await _assert_attempt_untouched(session, alert_id)


async def test_callback_edited_after_signing_is_refused(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # The signature covers the parameters, so a replayed-but-edited callback —
    # a real "delivered" turned into a "failed" to provoke rerouting — fails.
    alert_id, _ = await _dispatch_one_sms_household(client)
    signed = {"MessageSid": twilio.messages.sent[0].sid, "MessageStatus": "sent"}

    response = await client.post(
        "/webhooks/twilio/status",
        data=signed | {"MessageStatus": "delivered"},
        headers=twilio_signed_headers(signed),
    )

    assert response.status_code == 403
    await _assert_attempt_untouched(session, alert_id)


async def test_signature_is_checked_against_the_public_callback_url(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Twilio signs the URL it posted to, which is the PUBLIC_BASE_URL callback we
    # handed it — not the internal address the request arrives on.
    alert_id, _ = await _dispatch_one_sms_household(client)
    params = {"MessageSid": twilio.messages.sent[0].sid, "MessageStatus": "delivered"}

    response = await client.post(
        "/webhooks/twilio/status",
        data=params,
        headers=twilio_signed_headers(params, url="https://attacker.test/webhooks/twilio/status"),
    )

    assert response.status_code == 403
    await _assert_attempt_untouched(session, alert_id)


async def test_callback_is_refused_when_no_auth_token_is_configured(
    client: AsyncClient,
    session: AsyncSession,
    twilio: FakeTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail closed: a deployment that forgot the token gets a shut endpoint, not
    # an open one.
    alert_id, _ = await _dispatch_one_sms_household(client)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "")

    response = await _post_status(
        client, MessageSid=twilio.messages.sent[0].sid, MessageStatus="delivered"
    )

    assert response.status_code == 403
    await _assert_attempt_untouched(session, alert_id)


async def test_a_refused_callback_is_logged_without_the_auth_token(
    client: AsyncClient, twilio: FakeTwilio, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _dispatch_one_sms_household(client)
    recorder = RecordingLogger()
    monkeypatch.setattr(twilio_status, "logger", recorder)

    await _post_unsigned_status(
        client, MessageSid=twilio.messages.sent[0].sid, MessageStatus="delivered"
    )

    assert [(level, event) for level, event, _ in recorder.calls] == [
        ("warning", "twilio_status.invalid_signature")
    ]
    assert TWILIO_AUTH_TOKEN not in json.dumps(recorder.calls[0][2])


# --- Fallback rerouting ------------------------------------------------------
#
# The feature the project is named for: a terminal failure starts a *new*
# attempt on the household's next channel, from the webhook, without a human
# and without a poll.


async def _contents(session: AsyncSession, alert_id: str) -> list[AlertContent]:
    return list(
        (
            await session.exec(
                select(AlertContent).where(AlertContent.alert_id == uuid.UUID(alert_id))
            )
        ).all()
    )


async def _fail_first_attempt(client: AsyncClient, twilio: FakeTwilio, **params: str) -> Any:
    """Report the first SMS as undelivered, the way Twilio does."""
    return await _post_status(
        client,
        MessageSid=twilio.messages.sent[0].sid,
        MessageStatus="undelivered",
        ErrorCode="30006",
        ErrorMessage="Landline or unreachable carrier",
        **params,
    )


async def test_a_failed_attempt_starts_a_new_one_on_the_next_channel(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, household_id = await _dispatch_one_sms_household(client)

    await _fail_first_attempt(client, twilio)

    attempts = await _attempts(session, alert_id)
    assert [(a.channel, a.attempt_number, a.status) for a in attempts] == [
        ("sms", 1, "failed"),
        # The household's fallback_channel_order is ["voice"], so attempt 1's
        # successor is its first entry.
        ("voice", 2, "queued"),
    ]
    assert all(a.household_id == uuid.UUID(household_id) for a in attempts)


async def test_the_failed_attempt_is_left_exactly_as_it_was(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Domain Rule 2: a fallback is a new row, never a retry written over the old
    # one. The failed row is the evidence that SMS was tried at all.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _fail_first_attempt(client, twilio)

    failed = (await _attempts(session, alert_id))[0]
    await session.refresh(failed)
    assert failed.status == "failed"
    assert failed.channel == "sms"
    assert failed.attempt_number == 1
    assert failed.twilio_sid == sid
    assert failed.error_reason == "30006: Landline or unreachable carrier"
    assert failed.completed_at is not None


async def test_an_in_flight_status_never_reroutes(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # queued/sending are the delivery still happening. A fallback fired on one
    # of these would race the message it is giving up on.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="queued")
    await _post_status(client, MessageSid=sid, MessageStatus="sending")

    assert len(await _attempts(session, alert_id)) == 1


async def test_a_delivered_status_never_reroutes(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client)

    await _post_status(client, MessageSid=twilio.messages.sent[0].sid, MessageStatus="delivered")

    assert len(await _attempts(session, alert_id)) == 1


async def test_a_retried_failure_callback_reroutes_only_once(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Twilio retries, and it has two names for one failure. Either would be a
    # second reroute — and two fallback rows for one failure — without the
    # idempotency guard the reroute hangs off.
    alert_id, _ = await _dispatch_one_sms_household(client)
    sid = twilio.messages.sent[0].sid

    await _fail_first_attempt(client, twilio)
    await _fail_first_attempt(client, twilio)
    await _post_status(client, MessageSid=sid, MessageStatus="failed")

    attempts = await _attempts(session, alert_id)
    assert [(a.channel, a.attempt_number) for a in attempts] == [("sms", 1), ("voice", 2)]


async def test_the_fallback_channel_gets_content_generated_for_it(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio, claude_calls: list[Any]
) -> None:
    alert_id, household_id = await _dispatch_one_sms_household(client)

    await _fail_first_attempt(client, twilio)

    # Generated for the channel being rerouted *onto*: what reads well as an SMS
    # is not what a voice call should say.
    assert _requested_channels(claude_calls) == ["sms", "voice"]
    contents = await _contents(session, alert_id)
    assert sorted(c.channel for c in contents) == ["sms", "voice"]
    assert all(c.household_id == uuid.UUID(household_id) for c in contents)


async def test_content_already_written_for_the_fallback_channel_is_reused(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio, claude_calls: list[Any]
) -> None:
    alert_id, household_id = await _dispatch_one_sms_household(client)
    session.add(
        AlertContent(
            alert_id=uuid.UUID(alert_id),
            household_id=uuid.UUID(household_id),
            channel="voice",
            generated_text=SMS_TEXT,
            generated_script="Already written for voice.",
            video_caption_text=SMS_TEXT,
            language="en",
        )
    )
    await session.commit()

    await _fail_first_attempt(client, twilio)

    # Only the dispatch's own generation ran: a reroute does not pay for content
    # this alert already has for that channel.
    assert _requested_channels(claude_calls) == ["sms"]
    assert len(await _contents(session, alert_id)) == 2


async def test_a_fallback_onto_sms_is_dispatched_immediately(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Voice (#16) and ASL/video (#19) cannot be sent on yet, so a second SMS is
    # the one fallback this build can carry all the way to Twilio.
    alert_id, _ = await _dispatch_one_sms_household(client, fallback_channel_order=["sms"])

    await _fail_first_attempt(client, twilio)

    assert len(twilio.messages.sent) == 2
    fallback = (await _attempts(session, alert_id))[1]
    assert fallback.channel == "sms"
    assert fallback.attempt_number == 2
    assert fallback.twilio_sid == twilio.messages.sent[1].sid


async def test_a_fallback_onto_a_channel_this_build_cannot_send_stays_queued(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # The row is created because the chain really did move onto voice; nothing
    # is sent, because voice is #16. Marking it failed instead would walk the
    # household down its chain for a channel that was never tried.
    alert_id, _ = await _dispatch_one_sms_household(client)

    await _fail_first_attempt(client, twilio)

    assert len(twilio.messages.sent) == 1
    fallback = (await _attempts(session, alert_id))[1]
    assert fallback.status == "queued"
    assert fallback.twilio_sid is None


async def test_each_failure_walks_one_step_further_down_the_chain(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Two SMS fallbacks deep, so every step can actually be sent and failed.
    alert_id, _ = await _dispatch_one_sms_household(client, fallback_channel_order=["sms", "sms"])

    for index in range(3):
        await _post_status(
            client, MessageSid=twilio.messages.sent[index].sid, MessageStatus="failed"
        )

    attempts = await _attempts(session, alert_id)
    assert [(a.channel, a.attempt_number) for a in attempts] == [
        ("sms", 1),
        ("sms", 2),
        ("sms", 3),
    ]
    # The chain is three long and stops there: the third failure has nowhere
    # left to go, which is what marks the household unreached below.
    assert [a.status for a in attempts] == ["failed", "failed", "failed"]


async def test_a_household_with_no_fallback_channels_gets_no_second_attempt(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client, fallback_channel_order=[])

    await _fail_first_attempt(client, twilio)

    assert len(await _attempts(session, alert_id)) == 1


async def test_rerouting_is_scheduled_as_a_task_rather_than_done_inline(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Domain Rule 3, and the reason Twilio gets its 200 fast: the handler
    # schedules the reroute and returns. Calling the handler directly is the
    # only way to see the task it hands back.
    await _dispatch_one_sms_household(client)
    background = BackgroundTasks()

    ack = await twilio_status.twilio_status(
        background=background,
        params={"MessageSid": twilio.messages.sent[0].sid, "MessageStatus": "failed"},
        session=session,
    )

    assert ack.result == "applied"
    assert [task.func for task in background.tasks] == [delivery_service.reroute_failed_attempt]


async def test_an_in_flight_status_schedules_no_task_at_all(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    await _dispatch_one_sms_household(client)
    background = BackgroundTasks()

    await twilio_status.twilio_status(
        background=background,
        params={"MessageSid": twilio.messages.sent[0].sid, "MessageStatus": "sending"},
        session=session,
    )

    assert background.tasks == []


async def test_rerouting_has_exactly_one_trigger_in_the_whole_codebase() -> None:
    # Domain Rule 3 is a statement about the codebase, not just this module: no
    # cron, no scheduler, no sweep of failed attempts — one webhook-driven task.
    backend = pathlib.Path(__file__).resolve().parent.parent
    sources = {
        path: path.read_text()
        for path in (backend / "app").rglob("*.py")
        if "__pycache__" not in path.parts
    }

    callers = sorted(
        path.name for path, source in sources.items() if "reroute_failed_attempt" in source
    )
    assert callers == ["delivery_service.py", "twilio_status.py"]
    assert "schedule" not in (backend / "pyproject.toml").read_text().lower()


async def test_the_guaranteed_fail_seed_household_is_rerouted_onto_voice(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # The fixture from #3 that exists to make this path provable: Twilio's
    # undeliverable-SMS number, with a two-deep fallback order.
    zone, households = await seed_demo_data(session)
    okonkwo = next(h for h in households if h.phone_number == TWILIO_UNDELIVERABLE_SMS_NUMBER)
    alert_id = await _alert(client, str(zone.id))
    await client.post(f"/alerts/{alert_id}/dispatch")
    sid = next(
        message.sid
        for message in twilio.messages.sent
        if message.params["to"] == TWILIO_UNDELIVERABLE_SMS_NUMBER
    )

    await _post_status(
        client,
        MessageSid=sid,
        MessageStatus="undelivered",
        ErrorCode="21614",
        ErrorMessage="To number is not a valid mobile number",
    )

    attempts = [
        attempt
        for attempt in await _attempts(session, alert_id)
        if attempt.household_id == okonkwo.id
    ]
    assert [(a.channel, a.attempt_number, a.status) for a in attempts] == [
        ("sms", 1, "failed"),
        ("voice", 2, "queued"),
    ]


async def test_a_reroute_for_an_attempt_that_vanished_is_logged_not_raised(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing awaits the task, so an exception in it would be swallowed by the
    # event loop. It has to report its own failures.
    recorder = RecordingLogger()
    monkeypatch.setattr(delivery_service, "logger", recorder)

    await delivery_service.reroute_failed_attempt(uuid.uuid4())

    assert [(level, event) for level, event, _ in recorder.calls] == [
        ("error", "delivery.reroute_attempt_missing")
    ]


# --- Fallback exhaustion -----------------------------------------------------
#
# The end of the chain. Domain Rule 4: a household whose every channel has
# failed is flagged for a person, never dropped quietly — "failing silently is
# the worst possible outcome here."


async def _household_row(session: AsyncSession, household_id: str) -> Household:
    household = await session.get(Household, uuid.UUID(household_id))
    assert household is not None
    await session.refresh(household)
    return household


async def test_a_household_whose_last_channel_fails_is_marked_unreached(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # No fallbacks at all, so the preferred channel's failure is already the end
    # of this household's chain.
    _, household_id = await _dispatch_one_sms_household(client, fallback_channel_order=[])

    await _fail_first_attempt(client, twilio)

    assert (await _household_row(session, household_id)).last_known_status == "unreached"


async def test_a_household_with_a_channel_left_is_not_marked_unreached(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # The chain continues onto voice, so nothing about this household needs a
    # human yet — calling it unreached here would send a dispatcher to a door
    # the system is still trying to reach by phone.
    _, household_id = await _dispatch_one_sms_household(client)

    await _fail_first_attempt(client, twilio)

    assert (await _household_row(session, household_id)).last_known_status == "unknown"


async def test_the_whole_chain_has_to_run_out_before_the_household_is_unreached(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Two SMS fallbacks deep, so every step can actually be sent and failed.
    _, household_id = await _dispatch_one_sms_household(
        client, fallback_channel_order=["sms", "sms"]
    )

    seen = []
    for index in range(3):
        await _post_status(
            client, MessageSid=twilio.messages.sent[index].sid, MessageStatus="failed"
        )
        seen.append((await _household_row(session, household_id)).last_known_status)

    # Only the failure with nothing after it flips the household.
    assert seen == ["unknown", "unknown", "unreached"]


async def test_marking_a_household_unreached_leaves_its_attempts_alone(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    # Domain Rule 2: the chain of rows is the audit trail of what was tried, and
    # the household-level verdict is written beside it, never over it.
    alert_id, _ = await _dispatch_one_sms_household(client, fallback_channel_order=["sms"])

    for index in range(2):
        await _post_status(
            client, MessageSid=twilio.messages.sent[index].sid, MessageStatus="failed"
        )

    attempts = await _attempts(session, alert_id)
    assert [(a.channel, a.attempt_number, a.status) for a in attempts] == [
        ("sms", 1, "failed"),
        ("sms", 2, "failed"),
    ]
    assert all(a.completed_at is not None for a in attempts)


async def test_an_unreached_household_needs_no_further_attempt(
    client: AsyncClient, session: AsyncSession, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch_one_sms_household(client, fallback_channel_order=[])

    await _fail_first_attempt(client, twilio)

    assert len(await _attempts(session, alert_id)) == 1
    assert len(twilio.messages.sent) == 1


async def test_a_snapshot_taken_afterwards_still_shows_who_needs_following_up(
    client: AsyncClient, twilio: FakeTwilio
) -> None:
    # A dispatcher who reloads mid-incident must still see the red rows: the
    # WebSocket event is gone by then, and the snapshot is all the page has.
    alert_id, household_id = await _dispatch_one_sms_household(client, fallback_channel_order=[])
    await _fail_first_attempt(client, twilio)

    body = (await client.get(f"/alerts/{alert_id}/status")).json()

    row = next(row for row in body["households"] if row["household_id"] == household_id)
    assert row["last_known_status"] == "unreached"
    # The failed attempt is still what the row shows; "unreached" is the
    # household's state, not the attempt's.
    assert row["current_attempt"]["status"] == "failed"


async def test_exhausting_a_chain_is_logged_as_a_warning_against_its_household(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, twilio: FakeTwilio
) -> None:
    # An operator reading the log during an incident needs this line to stand
    # out and to name the household it is about.
    recorder = RecordingLogger()
    monkeypatch.setattr(delivery_service, "logger", recorder)
    alert_id, household_id = await _dispatch_one_sms_household(client, fallback_channel_order=[])

    await _fail_first_attempt(client, twilio)

    level, _, context = next(
        call for call in recorder.calls if call[1] == "delivery.fallback_exhausted"
    )
    assert level == "warning"
    assert context["alert_id"] == alert_id
    assert context["household_id"] == household_id
    assert context["channel"] == "sms"
    assert context["attempt_number"] == 1
    assert context["last_known_status"] == "unreached"


# --- The status snapshot -----------------------------------------------------


async def test_status_snapshot_reports_every_households_current_attempt(
    client: AsyncClient, twilio: FakeTwilio
) -> None:
    alert_id, household_id = await _dispatch_one_sms_household(client)
    await _post_status(client, MessageSid=twilio.messages.sent[0].sid, MessageStatus="delivered")

    response = await client.get(f"/alerts/{alert_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["alert_id"] == alert_id
    assert body["status"] == "dispatching"
    assert len(body["households"]) == 1
    row = body["households"][0]
    assert row["household_id"] == household_id
    assert row["name"] == "Baker household"
    assert row["preferred_channel"] == "sms"
    assert row["last_known_status"] == "unknown"
    assert row["current_attempt"]["status"] == "delivered"
    assert row["current_attempt"]["channel"] == "sms"
    assert row["current_attempt"]["attempt_number"] == 1
    assert row["current_attempt"]["completed_at"]


async def test_status_snapshot_includes_households_with_no_attempt_yet(
    client: AsyncClient,
) -> None:
    # A household missing from the snapshot is a household nobody is watching.
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    await _household(client, zone_id, "+15550100301", name="Alvarez household")

    body = (await client.get(f"/alerts/{alert_id}/status")).json()

    assert len(body["households"]) == 1
    assert body["households"][0]["current_attempt"] is None


async def test_status_snapshot_covers_only_the_alerts_own_zone(client: AsyncClient) -> None:
    zone_id = await _zone(client)
    other_zone_id = await _zone(client, name="Hill District")
    alert_id = await _alert(client, zone_id)
    await _household(client, zone_id, "+15550100401", name="Baker household")
    await _household(client, other_zone_id, "+15550100402", name="Whitfield household")

    body = (await client.get(f"/alerts/{alert_id}/status")).json()

    assert [row["name"] for row in body["households"]] == ["Baker household"]


async def test_status_snapshot_for_an_unknown_alert_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/alerts/{uuid.uuid4()}/status")

    assert response.status_code == 404
