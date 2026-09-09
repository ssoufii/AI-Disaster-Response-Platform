"""Alert table model.

The Alert is the unit of work the whole pipeline hangs off: ``content_generator``
reads ``raw_message`` + ``severity``, every DeliveryAttempt references ``alert_id``,
and every WebSocket event keys off it.

An alert starts its life as ``draft`` — drafting and dispatching are deliberately
separate, so "an alert exists" never means "an alert is being sent."

As with Household, the enum-valued columns store the enum *value* as a plain
string; the schemas in ``app/schemas/`` are the validation boundary.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.enums import AlertStatus


class Alert(SQLModel, table=True):
    __tablename__ = "alert"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    raw_message: str
    severity: str
    zone_id: uuid.UUID = Field(foreign_key="zone.id", index=True)
    # Free-form dispatcher identifier until console auth lands (issue #20).
    created_by: str | None = Field(default=None)
    # Timezone-aware: every timestamp this system emits is UTC with an offset
    # (the WebSocket contract's timestamps are ISO-8601 Z), so the column must
    # keep the offset rather than silently truncating it to a naive value.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    status: str = Field(default=AlertStatus.DRAFT.value)
