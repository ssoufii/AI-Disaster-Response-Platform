"""Nobody reads a dispatch, orders one, or watches one go out without the token.

Three things are being protected, and they are not the same thing:

- The **household register** is the PII. An unauthenticated ``GET /households``
  would hand over every phone number in the system, which would make the
  redaction work in ``test_redaction.py`` beside the point.
- **Dispatching** spends money and places real calls to real people.
- The **socket** is a live feed of the same data the snapshot returns, so guarding
  one and not the other guards neither.

And two things deliberately are not: the Twilio webhooks, which authenticate
every request by signature because Twilio cannot present a token of ours, and
``/health``, which says only whether the process is up.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.auth import WS_UNAUTHORIZED_CODE
from app.config import settings
from app.services import dispatcher_ws
from tests.conftest import DISPATCHER_API_TOKEN, dispatcher_headers, twilio_signed_headers
from tests.test_dispatcher_ws import FakeWebSocket

# The reads the console makes, one per router. Parametrised rather than written
# out per endpoint so the guard is asserted on the router it was attached to
# rather than on one route that happened to be checked.
#
# ``/zones/{id}/households`` is the one that matters most: it is the household
# register, and an unauthenticated read of it would hand over every phone number
# in the system.
PII_READ = f"/zones/{uuid.uuid4()}/households"
GUARDED_READS = [
    PII_READ,
    f"/alerts/{uuid.uuid4()}",
    f"/alerts/{uuid.uuid4()}/status",
]


@pytest.mark.parametrize("path", GUARDED_READS)
async def test_a_read_without_a_token_is_refused(anonymous_client: AsyncClient, path: str) -> None:
    response = await anonymous_client.get(path)

    assert response.status_code == 401
    # The challenge is what tells a client this is a credential problem and not
    # the alert being missing.
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", GUARDED_READS)
async def test_the_same_read_with_the_token_is_served(client: AsyncClient, path: str) -> None:
    response = await client.get(path)

    # A 404 for an alert that was never drafted is the route doing its job; what
    # matters is that the request got past the guard to find that out.
    assert response.status_code in (200, 404)


async def test_dispatching_without_a_token_is_refused(anonymous_client: AsyncClient) -> None:
    """The costly one: this route places calls and sends messages to real people."""
    response = await anonymous_client.post(f"/alerts/{uuid.uuid4()}/dispatch")

    assert response.status_code == 401


async def test_drafting_an_alert_without_a_token_is_refused(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.post(
        "/alerts",
        json={
            "title": "Forged alert",
            "raw_message": "Evacuate immediately.",
            "severity": "evacuate_now",
            "zone_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 401


async def test_registering_a_household_without_a_token_is_refused(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.post(
        "/households",
        json={
            "name": "Baker household",
            "phone_number": "+15550100901",
            "zone_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong-token",
        f"Basic {DISPATCHER_API_TOKEN}",  # right secret, wrong scheme
        DISPATCHER_API_TOKEN,  # no scheme at all
        "Bearer ",
        "Bearer",
    ],
)
async def test_a_credential_that_is_not_the_token_is_refused(
    anonymous_client: AsyncClient, authorization: str
) -> None:
    response = await anonymous_client.get(PII_READ, headers={"Authorization": authorization})

    assert response.status_code == 401


async def test_a_credential_with_non_ascii_in_it_is_refused_not_crashed(
    anonymous_client: AsyncClient,
) -> None:
    """A wrong token is a 401 whatever alphabet it is written in.

    ``compare_digest`` refuses two ``str`` arguments when either holds a
    non-ASCII character, so comparing the raw strings would turn this into a 500
    and a traceback — an error page for what is simply a wrong password.

    Sent as latin-1 bytes because that is how it would actually arrive: header
    bytes are decoded as latin-1, so the handler receives a ``str`` with
    non-ASCII characters in it even though no well-behaved client would compose
    one.
    """
    response = await anonymous_client.get(
        PII_READ, headers={"Authorization": "Bearer tökén".encode("latin-1")}
    )

    assert response.status_code == 401


async def test_an_unconfigured_deployment_serves_nobody(
    anonymous_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No token configured means the console is closed, not that the check is off.

    A 503 rather than a 401, because retrying with a different credential will
    never help: nothing can authenticate here until someone sets the variable.
    """
    monkeypatch.setattr(settings, "DISPATCHER_API_TOKEN", "")

    response = await anonymous_client.get(PII_READ, headers=dispatcher_headers())

    assert response.status_code == 503


async def test_health_needs_no_token(anonymous_client: AsyncClient) -> None:
    """A liveness probe has no credential to present and nothing to learn."""
    response = await anonymous_client.get("/health")

    assert response.status_code == 200


async def test_the_twilio_webhook_is_still_reachable_without_a_dispatcher_token(
    anonymous_client: AsyncClient,
) -> None:
    """Twilio posts from the public internet and authenticates by signature.

    Putting the dispatcher token in front of this endpoint would not secure it —
    it would silence every delivery callback the system depends on.
    """
    params = {"MessageSid": f"SM{uuid.uuid4().hex}", "MessageStatus": "delivered"}

    response = await anonymous_client.post(
        "/webhooks/twilio/status", data=params, headers=twilio_signed_headers(params)
    )

    # 200 with "unknown twilio_sid": no attempt carries this SID. The point is
    # that it was processed rather than refused for want of a bearer token.
    assert response.status_code == 200


async def test_an_unsigned_webhook_is_still_refused(anonymous_client: AsyncClient) -> None:
    """Unguarded by the dispatcher token is not unguarded."""
    response = await anonymous_client.post(
        "/webhooks/twilio/status",
        data={"MessageSid": f"SM{uuid.uuid4().hex}", "MessageStatus": "delivered"},
    )

    assert response.status_code == 403


async def test_the_default_token_is_empty_so_a_deployment_must_set_one() -> None:
    # Read off the field rather than live settings, so a developer's own .env
    # does not decide whether this passes.
    assert type(settings).model_fields["DISPATCHER_API_TOKEN"].default == ""


# --- The socket --------------------------------------------------------------
#
# A refused socket must be refused at the handshake. Dropping an unauthenticated
# subscriber a moment after accepting it would still have put it in the registry,
# where the next broadcast would find it.


@pytest.fixture(autouse=True)
def manager() -> dispatcher_ws.ConnectionManager:
    """A connection manager with no subscribers left over from another test."""
    dispatcher_ws.manager = dispatcher_ws.ConnectionManager()
    return dispatcher_ws.manager


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
async def test_a_socket_without_the_token_is_closed_before_it_subscribes(
    manager: dispatcher_ws.ConnectionManager, token: str | None
) -> None:
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket()

    await dispatcher_ws.alert_updates(websocket, alert_id, token=token)

    assert not websocket.accepted
    assert websocket.closed_with == WS_UNAUTHORIZED_CODE
    assert manager.subscriber_count(alert_id) == 0


async def test_a_refused_socket_receives_no_delivery_update(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    """The whole point of refusing at the handshake, stated as the outcome."""
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket()

    await dispatcher_ws.alert_updates(websocket, alert_id, token="wrong-token")
    await manager.broadcast(
        dispatcher_ws.DeliveryUpdateEvent(
            alert_id=alert_id,
            household_id=uuid.uuid4(),
            channel=dispatcher_ws.Channel.SMS,
            status=dispatcher_ws.DeliveryStatus.DELIVERED,
            attempt_number=1,
        )
    )

    assert websocket.sent == []


async def test_a_socket_with_the_token_subscribes(
    manager: dispatcher_ws.ConnectionManager,
) -> None:
    alert_id = uuid.uuid4()
    websocket = FakeWebSocket()
    seen: list[int] = []

    original_disconnect = manager.disconnect

    def record_then_disconnect(*args: object) -> None:
        seen.append(manager.subscriber_count(alert_id))
        original_disconnect(*args)  # type: ignore[arg-type]

    manager.disconnect = record_then_disconnect  # type: ignore[method-assign]
    await dispatcher_ws.alert_updates(websocket, alert_id, token=DISPATCHER_API_TOKEN)

    assert websocket.accepted
    assert websocket.closed_with is None
    # It was a subscriber while it was connected, and is not one now.
    assert seen == [1]
    assert manager.subscriber_count(alert_id) == 0


async def test_an_unconfigured_deployment_accepts_no_socket_either(
    manager: dispatcher_ws.ConnectionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DISPATCHER_API_TOKEN", "")
    websocket = FakeWebSocket()

    await dispatcher_ws.alert_updates(websocket, uuid.uuid4(), token=DISPATCHER_API_TOKEN)

    assert not websocket.accepted
    assert websocket.closed_with == WS_UNAUTHORIZED_CODE
