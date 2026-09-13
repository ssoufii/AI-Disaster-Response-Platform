"""Request/response schemas for alerts."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import AlertStatus, Severity


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
    """What the content-generation phase of a dispatch did.

    ``content_generated`` counts the households generated for on this call;
    households that already had content for this alert are counted in
    ``households`` but not regenerated, so dispatching twice does not produce a
    second AlertContent row for anyone.
    """

    alert_id: uuid.UUID
    status: AlertStatus
    households: int
    content_generated: int


class AlertRead(BaseModel):
    id: uuid.UUID
    title: str
    raw_message: str
    severity: Severity
    facts: dict[str, Any]
    zone_id: uuid.UUID
    created_by: str | None = None
    created_at: datetime
    status: AlertStatus
