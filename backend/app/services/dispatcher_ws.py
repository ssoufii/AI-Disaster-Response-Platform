"""The dispatcher console's live feed.

Backs ``WS /ws/alerts/{alert_id}``: a console subscribes to one alert and
receives every delivery state change on it as it happens.

The feed is a diff channel, never the source of truth. The console loads
``GET /alerts/{id}/status`` first and only then subscribes, so the grid is
populated before the socket connects rather than blank while it does
(CLAUDE.md, WebSocket Contract). Nothing here replays history to a late
subscriber; a console that connects mid-dispatch has already read the snapshot,
and its next event applies on top of it.

Subscribers are held per ``alert_id`` and in memory. Two consequences worth
being explicit about:

1. **A dead socket is dropped, never raised.** Broadcasting happens on the
   webhook's path (and the dispatch path), and a browser tab that closed
   mid-dispatch must not turn into a 500 on a Twilio callback or strand the
   households behind it.
2. **One process.** A second uvicorn worker would have its own registry and its
   consoles would not see these events. Scaling the socket layer out is
   explicitly out of scope for this story; a single instance is assumed.
"""

import uuid
from collections import defaultdict

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import Channel, DeliveryStatus
from app.schemas.ws_events import DeliveryUpdateEvent

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Which consoles are watching which alert."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)

    async def connect(self, alert_id: uuid.UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[alert_id].add(websocket)

    def disconnect(self, alert_id: uuid.UUID, websocket: WebSocket) -> None:
        """Forget one socket. Safe to call for a socket already forgotten."""
        subscribers = self._connections.get(alert_id)
        if subscribers is None:
            return
        subscribers.discard(websocket)
        if not subscribers:
            del self._connections[alert_id]

    def subscriber_count(self, alert_id: uuid.UUID) -> int:
        return len(self._connections.get(alert_id, ()))

    async def broadcast(self, event: DeliveryUpdateEvent) -> None:
        """Send one event to every console watching its alert.

        Iterates a copy of the subscriber set, because a send that fails removes
        its socket from the set it is being read from.
        """
        payload = event.model_dump(mode="json")
        for websocket in list(self._connections.get(event.alert_id, ())):
            try:
                await websocket.send_json(payload)
            except Exception as exc:
                # A closed tab, a dropped connection, a half-open socket: all of
                # them mean this console is gone, and none of them are this
                # dispatch's problem. Drop it and keep broadcasting to the rest.
                self.disconnect(event.alert_id, websocket)
                logger.warning(
                    "dispatcher_ws.send_failed",
                    alert_id=str(event.alert_id),
                    household_id=str(event.household_id),
                    error_type=type(exc).__name__,
                )


# Process-wide, because the sockets it holds are this process's.
manager = ConnectionManager()


async def broadcast_delivery_update(
    attempt: DeliveryAttempt,
    *,
    fallback_triggered: bool = False,
    fallback_channel: Channel | None = None,
) -> None:
    """Push one attempt's current state to the consoles watching its alert.

    Takes the attempt row rather than loose fields so the event can never
    describe a state the audit trail does not hold — what the console renders
    and what ``GET /alerts/{id}/status`` would return are read from the same
    row.

    Call this *after* the change is committed. An event that beat its own commit
    would leave a console showing a state that a reconnect (#11) then resyncs
    away.

    ``fallback_triggered``/``fallback_channel`` annotate the attempt that
    *failed*, not the one that replaces it — the contract's fallback event says
    "this sms attempt failed and is being retried on voice", which is what lets
    the console write "SMS failed → retrying via Voice" (#14) from one event.
    The replacement attempt announces itself separately, as any new attempt
    does.
    """
    event = DeliveryUpdateEvent(
        alert_id=attempt.alert_id,
        household_id=attempt.household_id,
        channel=Channel(attempt.channel),
        status=DeliveryStatus(attempt.status),
        attempt_number=attempt.attempt_number,
        fallback_triggered=fallback_triggered,
        fallback_channel=fallback_channel,
    )
    await manager.broadcast(event)
    logger.info(
        "dispatcher_ws.delivery_update",
        alert_id=str(attempt.alert_id),
        household_id=str(attempt.household_id),
        channel=attempt.channel,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        fallback_triggered=fallback_triggered,
        fallback_channel=fallback_channel.value if fallback_channel else None,
        subscribers=manager.subscriber_count(attempt.alert_id),
    )


@router.websocket("/ws/alerts/{alert_id}")
async def alert_updates(websocket: WebSocket, alert_id: uuid.UUID) -> None:
    """Subscribe one console to one alert's delivery updates.

    The alert is not looked up here. The console has already fetched the
    snapshot for this id — a 404 there is where a wrong id is caught — and a
    subscription to an alert that does not exist simply never receives anything.
    """
    await manager.connect(alert_id, websocket)
    log = logger.bind(alert_id=str(alert_id))
    log.info("dispatcher_ws.connected", subscribers=manager.subscriber_count(alert_id))

    try:
        while True:
            # The console never sends anything. This waits for the close frame,
            # which arrives as WebSocketDisconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(alert_id, websocket)
        log.info("dispatcher_ws.disconnected", subscribers=manager.subscriber_count(alert_id))
