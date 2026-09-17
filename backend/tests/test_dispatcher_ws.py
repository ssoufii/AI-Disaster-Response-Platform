"""The dispatcher console's live feed.

Covers the connection manager, the ``WS /ws/alerts/{alert_id}`` endpoint, and
the two places a ``delivery_update`` is broadcast from: the Twilio status
webhook and the dispatch itself.

The sockets here are fakes rather than real WebSocket handshakes. What matters
to the console is which payload reaches which subscriber, and a fake records
that directly — without a second event loop running the ASGI app alongside the
async client these tests already drive. Twilio and Anthropic are replaced as
everywhere else (CLAUDE.md, Testing).
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from httpx import AsyncClient

from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import Channel, DeliveryStatus
from app.schemas.ws_events import DeliveryUpdateEvent
from app.services import content_generator, dispatcher_ws
from tests.conftest import FakeTwilio, twilio_signed_headers

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


@pytest.fixture(autouse=True)
def manager() -> dispatcher_ws.ConnectionManager:
    """A connection manager with no subscribers left over from another test.

    Autouse because the manager is process-wide: a socket one test forgot to
    disconnect would otherwise still be listening in the next one.
    """
    dispatcher_ws.manager = dispatcher_ws.ConnectionManager()
    return dispatcher_ws.manager


class FakeWebSocket:
    """A console socket, recording what was sent to it instead of framing it.

    ``error`` makes the next send raise, which is how a test exercises a console
    that went away mid-dispatch. ``incoming`` is what ``receive_text`` yields
    before reporting the close; the console sends nothing, so it is normally
    empty.
    """

    def __init__(self, incoming: list[str] | None = None, error: Exception | None = None) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.incoming = list(incoming or [])
        self.error = error

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(payload)

    async def receive_text(self) -> str:
        if self.incoming:
            return self.incoming.pop(0)
        raise WebSocketDisconnect(code=1000)


def _event(alert_id: uuid.UUID, household_id: uuid.UUID | None = None) -> DeliveryUpdateEvent:
    return DeliveryUpdateEvent(
        alert_id=alert_id,
        household_id=household_id or uuid.uuid4(),
        channel=Channel.SMS,
        status=DeliveryStatus.DELIVERED,
        attempt_number=1,
    )


# --- Connection manager ------------------------------------------------------


async def test_broadcast_reaches_every_console_watching_that_alert(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    alert_id = uuid.uuid4()
    first, second = FakeWebSocket(), FakeWebSocket()
    await manager.connect(alert_id, first)
    await manager.connect(alert_id, second)

    await manager.broadcast(_event(alert_id))

    assert first.accepted and second.accepted
    assert len(first.sent) == 1
    assert len(second.sent) == 1


async def test_broadcast_does_not_leak_to_another_alerts_console(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    watched, other = uuid.uuid4(), uuid.uuid4()
    subscriber, bystander = FakeWebSocket(), FakeWebSocket()
    await manager.connect(watched, subscriber)
    await manager.connect(other, bystander)

    await manager.broadcast(_event(watched))

    assert len(subscriber.sent) == 1
    assert bystander.sent == []


async def test_disconnected_console_stops_receiving(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket()
    await manager.connect(alert_id, websocket)
    manager.disconnect(alert_id, websocket)

    await manager.broadcast(_event(alert_id))

    assert websocket.sent == []
    assert manager.subscriber_count(alert_id) == 0


async def test_a_dead_console_is_dropped_and_the_rest_still_get_the_event(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    """A tab that closed mid-dispatch must not cost the other consoles an event."""
    alert_id = uuid.uuid4()
    dead = FakeWebSocket(error=RuntimeError("connection closed"))
    alive = FakeWebSocket()
    await manager.connect(alert_id, dead)
    await manager.connect(alert_id, alive)

    await manager.broadcast(_event(alert_id))

    assert len(alive.sent) == 1
    assert manager.subscriber_count(alert_id) == 1


async def test_disconnecting_an_unknown_socket_is_a_no_op(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    manager.disconnect(uuid.uuid4(), FakeWebSocket())


# --- The endpoint ------------------------------------------------------------


async def test_endpoint_subscribes_on_connect_and_unsubscribes_on_close(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket()

    # Returns when the fake reports the close frame, which is the disconnect.
    await dispatcher_ws.alert_updates(websocket, alert_id)

    assert websocket.accepted
    assert manager.subscriber_count(alert_id) == 0


async def test_endpoint_keeps_the_subscription_open_while_the_console_is_connected(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    """Anything the console sends is ignored, not treated as a disconnect."""
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket(incoming=["ping"])
    seen: list[int] = []

    original_disconnect = manager.disconnect

    def record_then_disconnect(*args: Any) -> None:
        seen.append(len(websocket.incoming))
        original_disconnect(*args)

    manager.disconnect = record_then_disconnect  # type: ignore[method-assign]
    await dispatcher_ws.alert_updates(websocket, alert_id)

    # The subscription outlived the inbound message and ended only at the close.
    assert seen == [0]


# --- Broadcast from the status webhook ---------------------------------------


async def _zone(client: AsyncClient) -> str:
    return (await client.post("/zones", json={"name": "Riverside District"})).json()["id"]


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


async def _household(client: AsyncClient, zone_id: str) -> str:
    return (
        await client.post(
            "/households",
            json={
                "name": "Baker household",
                "phone_number": "+15550100001",
                "language": "en",
                "preferred_channel": "sms",
                "fallback_channel_order": ["voice"],
                "zone_id": zone_id,
            },
        )
    ).json()["id"]


async def _dispatch(client: AsyncClient) -> tuple[str, str]:
    """Draft, dispatch, and return ``(alert_id, household_id)``."""
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    household_id = await _household(client, zone_id)
    await client.post(f"/alerts/{alert_id}/dispatch")
    return alert_id, household_id


async def _post_status(client: AsyncClient, **params: str) -> Any:
    return await client.post(
        "/webhooks/twilio/status", data=params, headers=twilio_signed_headers(params)
    )


async def _watch(manager: dispatcher_ws.ConnectionManager, alert_id: str) -> FakeWebSocket:
    """Open a console on an alert, as the page does once it has its snapshot."""
    websocket = FakeWebSocket()
    await manager.connect(uuid.UUID(alert_id), websocket)
    return websocket


async def test_applied_callback_pushes_a_delivery_update(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager, twilio: FakeTwilio
) -> None:
    alert_id, household_id = await _dispatch(client)
    console = await _watch(manager, alert_id)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert len(console.sent) == 1
    event = console.sent[0]
    assert event["type"] == "delivery_update"
    assert event["alert_id"] == alert_id
    assert event["household_id"] == household_id
    assert event["status"] == "delivered"


async def test_event_carries_enough_state_to_be_applied_standalone(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager, twilio: FakeTwilio
) -> None:
    """A console that has seen no prior event must be able to apply this one."""
    alert_id, household_id = await _dispatch(client)
    console = await _watch(manager, alert_id)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    event = console.sent[0]
    assert set(event) == {
        "type",
        "alert_id",
        "household_id",
        "channel",
        "status",
        "attempt_number",
        "fallback_triggered",
        "fallback_channel",
        "timestamp",
    }
    assert event["channel"] == "sms"
    assert event["attempt_number"] == 1
    # Rerouting arrives with #12; until then the contract's fields are present
    # and empty rather than absent.
    assert event["fallback_triggered"] is False
    assert event["fallback_channel"] is None
    assert event["timestamp"].endswith("Z")


async def test_each_status_change_pushes_its_own_event(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager, twilio: FakeTwilio
) -> None:
    alert_id, _ = await _dispatch(client)
    console = await _watch(manager, alert_id)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="sending")
    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert [event["status"] for event in console.sent] == ["sending", "delivered"]


async def test_a_retried_callback_does_not_push_a_second_event(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager, twilio: FakeTwilio
) -> None:
    """Twilio retries; the console must not see the same change twice."""
    alert_id, _ = await _dispatch(client)
    console = await _watch(manager, alert_id)
    sid = twilio.messages.sent[0].sid

    await _post_status(client, MessageSid=sid, MessageStatus="delivered")
    await _post_status(client, MessageSid=sid, MessageStatus="delivered")

    assert len(console.sent) == 1


async def test_an_unplaceable_callback_pushes_nothing(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager
) -> None:
    alert_id, _ = await _dispatch(client)
    console = await _watch(manager, alert_id)

    await _post_status(client, MessageSid="SMnot-ours", MessageStatus="delivered")

    assert console.sent == []


# --- Broadcast from the dispatch itself --------------------------------------


async def test_a_console_open_when_a_dispatch_starts_sees_the_queued_attempt(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager
) -> None:
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    household_id = await _household(client, zone_id)
    console = await _watch(manager, alert_id)

    await client.post(f"/alerts/{alert_id}/dispatch")

    assert len(console.sent) == 1
    assert console.sent[0]["status"] == DeliveryStatus.QUEUED.value
    assert console.sent[0]["household_id"] == household_id


async def test_a_send_twilio_refuses_still_reaches_the_console(
    client: AsyncClient, manager: dispatcher_ws.ConnectionManager, twilio: FakeTwilio
) -> None:
    """No callback is ever coming for this attempt, so this is its only event."""
    zone_id = await _zone(client)
    alert_id = await _alert(client, zone_id)
    await _household(client, zone_id)
    console = await _watch(manager, alert_id)
    twilio.messages.error = RuntimeError("unroutable number")

    await client.post(f"/alerts/{alert_id}/dispatch")

    assert [event["status"] for event in console.sent] == [
        DeliveryStatus.QUEUED.value,
        DeliveryStatus.FAILED.value,
    ]


async def test_broadcast_reads_the_attempt_row_it_was_given(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    """The event describes the audit trail, not fields assembled beside it."""
    alert_id = uuid.uuid4()
    attempt = DeliveryAttempt(
        alert_id=alert_id,
        household_id=uuid.uuid4(),
        channel=Channel.VOICE.value,
        attempt_number=2,
        status=DeliveryStatus.NO_ANSWER.value,
    )
    console = FakeWebSocket()
    await manager.connect(alert_id, console)

    await dispatcher_ws.broadcast_delivery_update(attempt)

    assert console.sent[0]["channel"] == "voice"
    assert console.sent[0]["attempt_number"] == 2
    assert console.sent[0]["status"] == "no_answer"
