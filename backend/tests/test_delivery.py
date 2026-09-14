"""SMS dispatch, the Twilio status webhook, and the status snapshot.

The first end-to-end slice: dispatch → Twilio → status callback → DB → snapshot.
Twilio is replaced by the autouse ``twilio`` fixture in ``conftest``, so nothing
here sends a message (CLAUDE.md, Testing).
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.delivery_attempt import DeliveryAttempt
from app.services import content_generator
from tests.conftest import PUBLIC_BASE_URL, TWILIO_PHONE_NUMBER, FakeTwilio

SMS_TEXT = "Evacuate now. Go to Lincoln High School, 400 Oak St."


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every dispatch here generates content; none of it reaches Anthropic."""

    async def create(**_kwargs: Any) -> SimpleNamespace:
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
    """POST a Twilio status callback exactly as Twilio does: form-encoded."""
    return await client.post("/webhooks/twilio/status", data=params)


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
