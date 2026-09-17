"""Events pushed to the dispatcher console over its WebSocket.

Server → client only. The console sends nothing back; it reads
``GET /alerts/{id}/status`` for a snapshot and then applies these as diffs.

**Every event is applicable standalone** (CLAUDE.md, WebSocket Contract). A
console can connect halfway through a dispatch, or miss a frame and reconnect
(#11), so an event that only said "attempt 2 changed" would be unusable to the
client that never saw attempt 1. That is why each event repeats the household,
channel, attempt number and status in full rather than referring to an attempt
id the client would have to resolve.

``fallback_triggered`` and ``fallback_channel`` are part of the contract from
the start, and are false/null until rerouting lands (#12) — the console reads
them on every event and must not have to handle their sudden appearance.

This schema is one of three that have to move together: the TypeScript types in
``frontend/lib/types.ts`` and the contract in ``docs/architecture.md`` are the
other two.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from app.models.enums import Channel, DeliveryStatus


class DeliveryUpdateEvent(BaseModel):
    """One household's delivery state changed on one alert."""

    type: Literal["delivery_update"] = "delivery_update"
    alert_id: uuid.UUID
    household_id: uuid.UUID
    channel: Channel
    status: DeliveryStatus
    attempt_number: int
    fallback_triggered: bool = False
    fallback_channel: Channel | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_serializer("timestamp")
    def _serialize_timestamp(self, timestamp: datetime) -> str:
        """ISO-8601 with a ``Z``, as the contract spells it."""
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
