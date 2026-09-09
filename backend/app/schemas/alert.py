"""Request/response schemas for alerts."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AlertStatus, Severity


class AlertCreate(BaseModel):
    title: str = Field(min_length=1)
    raw_message: str = Field(min_length=1)
    # Anything outside advisory/warning/evacuate_now is a validation error, not a
    # silently accepted string — severity drives real behavior downstream.
    severity: Severity
    zone_id: uuid.UUID
    created_by: str | None = None


class AlertRead(BaseModel):
    id: uuid.UUID
    title: str
    raw_message: str
    severity: Severity
    zone_id: uuid.UUID
    created_by: str | None = None
    created_at: datetime
    status: AlertStatus
