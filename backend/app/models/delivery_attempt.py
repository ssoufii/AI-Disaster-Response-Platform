"""DeliveryAttempt table model.

One row per attempt to reach one household about one alert — the audit trail the
dispatcher console is built on.

**A row is never reused.** A fallback onto the next channel creates a *new* row
with an incremented ``attempt_number``; the failed row keeps its status, its
``error_reason``, and its timestamps forever (CLAUDE.md, Domain Rule 2). Mutating
an attempt to represent a retry would erase the evidence that the first channel
was tried at all, which during an incident review is exactly the question being
asked.

The only mutation an existing row takes is its own progression through Twilio's
status callbacks — queued → sending → delivered — which is the same attempt
still, not a new one.

As with the other models, the enum-valued columns store the enum *value* as a
plain string; ``app/schemas/`` is the validation boundary.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.enums import DeliveryStatus


class DeliveryAttempt(SQLModel, table=True):
    __tablename__ = "delivery_attempt"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_id: uuid.UUID = Field(foreign_key="alert.id", index=True)
    household_id: uuid.UUID = Field(foreign_key="household.id", index=True)
    channel: str
    # 1 for the household's preferred channel; incremented by each fallback.
    attempt_number: int = Field(default=1)
    status: str = Field(default=DeliveryStatus.QUEUED.value)
    # Null until Twilio accepts the send — a send that raises still leaves a row
    # behind. Indexed because the status webhook's only handle on an attempt is
    # the SID Twilio quotes back.
    twilio_sid: str | None = Field(default=None, index=True)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    # Set once the attempt reaches a terminal status, so "still in flight" is a
    # null rather than a guess derived from the status string.
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    error_reason: str | None = Field(default=None)
