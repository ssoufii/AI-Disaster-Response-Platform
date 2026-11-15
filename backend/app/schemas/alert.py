"""Request/response schemas for alerts."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import AlertStatus, Severity
from app.schemas.delivery_attempt import HouseholdDeliveryStatus, UtcDatetime


class AlertCreate(BaseModel):
    title: str = Field(min_length=1)
    raw_message: str = Field(min_length=1)
    # Anything outside advisory/warning/evacuate_now is a validation error, not a
    # silently accepted string — severity drives real behavior downstream.
    severity: Severity
    # Structured facts (shelter, routes, times) content generation copies
    # verbatim. Optional: an alert whose whole message is an instruction with no
    # addresses or times is legitimate.
    facts: dict[str, Any] = Field(default_factory=dict)
    zone_id: uuid.UUID
    created_by: str | None = None


class AlertDispatchRead(BaseModel):
    """What a dispatch's two phases did: generate, then deliver.

    ``content_generated`` counts the households generated for on this call and
    ``deliveries_started`` the attempts sent; households that already had
    content or an attempt for this alert are counted in ``households`` but not
    redone, so dispatching twice neither produces a second AlertContent row nor
    sends anyone the same warning twice.

    ``deliveries_started`` can trail ``households`` legitimately: only SMS is
    wired up so far, so households on a channel still ahead in the build order
    are counted but not attempted.
    """

    alert_id: uuid.UUID
    status: AlertStatus
    households: int
    content_generated: int
    deliveries_started: int


class AlertStatusRead(BaseModel):
    """The full delivery snapshot for one alert.

    What the dispatcher console loads on page open, *before* it subscribes to
    the WebSocket — so the grid is never blank while the socket connects.
    """

    alert_id: uuid.UUID
    status: AlertStatus
    households: list[HouseholdDeliveryStatus]


class AlertRead(BaseModel):
    id: uuid.UUID
    title: str
    raw_message: str
    severity: Severity
    facts: dict[str, Any]
    zone_id: uuid.UUID
    created_by: str | None = None
    created_at: UtcDatetime
    status: AlertStatus


class AlertLatestRead(AlertRead):
    """The most recently dispatched alert, with what a console needs to open it.

    ``zone_name`` saves the console a second request. ``next_dispatch_at`` is
    only set by the simulation demo's scheduler, so its console can say when
    the next mock alert is due; it is null everywhere else.
    """

    zone_name: str
    next_dispatch_at: datetime | None = None
