"""Events pushed to the dispatcher console over its WebSocket.

Server → client only. The console sends nothing back; it reads
``GET /alerts/{id}/status`` for a snapshot and then applies these as diffs.

**Every event is applicable standalone** (CLAUDE.md, WebSocket Contract). A
console can connect halfway through a dispatch, or miss a frame and reconnect
(#11), so an event that only said "attempt 2 changed" would be unusable to the
client that never saw attempt 1. That is why each event repeats the household,
channel, attempt number and status in full rather than referring to an attempt
id the client would have to resolve.

``fallback_triggered`` and ``fallback_channel`` annotate the attempt that
*failed*, naming the channel its household is being retried on; every other
event carries them as false/null. The console reads them on every event rather
than having to handle their sudden appearance.

These schemas are one of three things that have to move together: the
TypeScript types in ``frontend/lib/types.ts`` and the contract in
``docs/architecture.md`` are the other two.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from app.models.enums import Channel, DeliveryStatus, HouseholdStatus


class _AlertEvent(BaseModel):
    """What every event on an alert's socket carries.

    ``alert_id`` is how the connection manager picks the consoles to send to,
    and ``household_id`` is the key the console's grid is stored under, so no
    event can be broadcast without both.
    """

    alert_id: uuid.UUID
    household_id: uuid.UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_serializer("timestamp")
    def _serialize_timestamp(self, timestamp: datetime) -> str:
        """ISO-8601 with a ``Z``, as the contract spells it."""
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


class DeliveryUpdateEvent(_AlertEvent):
    """One household's delivery state changed on one alert."""

    type: Literal["delivery_update"] = "delivery_update"
    channel: Channel
    status: DeliveryStatus
    attempt_number: int
    fallback_triggered: bool = False
    fallback_channel: Channel | None = None


class HouseholdUnreachedEvent(_AlertEvent):
    """Every channel this household has was tried and none of them landed.

    The end of a fallback chain, and the one event on this socket that asks for
    a person rather than reporting a machine's progress: a household here gets
    no further attempt from this system, so someone has to go and knock on the
    door (CLAUDE.md, Domain Rule 4).

    It is a statement about the *household*, not about an attempt, which is why
    it carries ``last_known_status`` rather than a delivery status — the row
    turns red (#14) on what the household is, matching what
    ``GET /alerts/{id}/status`` reports for it on a reload.

    ``last_channel`` and ``attempts_made`` describe the chain that ran out, so a
    console that connected after the whole thing happened can still say what was
    tried and how many times without going back to the API.
    """

    type: Literal["household_unreached"] = "household_unreached"
    last_channel: Channel
    attempts_made: int
    last_known_status: Literal[HouseholdStatus.UNREACHED] = HouseholdStatus.UNREACHED


AlertEvent = DeliveryUpdateEvent | HouseholdUnreachedEvent
