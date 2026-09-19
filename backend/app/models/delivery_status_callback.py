"""DeliveryStatusCallback table model.

One row per delivery state Twilio has already told us about, and the reason the
status webhook can be hit twice with the same callback without applying it
twice.

Twilio retries a callback it does not get a timely 200 for — that is documented
behavior, not a fault. Without a record of what has already been applied, a
routine retry would write the same status a second time, and fire the fallback
for the same failure twice, leaving two
``DeliveryAttempt`` rows for one reroute. Domain Rule 2 exists to make the audit
trail trustworthy; a duplicated chain of attempts is exactly the corruption it
guards against.

The key is ``(twilio_sid, status)``:

* ``twilio_sid`` alone would be wrong — an attempt legitimately progresses
  ``queued`` → ``sending`` → ``delivered``, and each of those is a different
  callback about the same message.
* The ``status`` stored is the *mapped* ``DeliveryStatus``, not Twilio's raw
  string, because what must happen at most once is the **state change**. Twilio
  has two names for one outcome (``failed`` and ``undelivered`` both mean the
  send failed; ``sending`` and ``sent`` are both in flight), and keying on the
  raw string would let the second name through as a fresh event. The raw string
  is kept alongside for debugging, but it does not decide identity.

The uniqueness is declared on the table, not just checked in the handler: two
retries can arrive at the same moment, and a check-then-write would let both
past. The constraint is what actually makes the guard hold.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class DeliveryStatusCallback(SQLModel, table=True):
    __tablename__ = "delivery_status_callback"
    __table_args__ = (
        UniqueConstraint("twilio_sid", "status", name="uq_status_callback_sid_status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    delivery_attempt_id: uuid.UUID = Field(foreign_key="delivery_attempt.id", index=True)
    twilio_sid: str
    # The mapped DeliveryStatus value — see the module docstring on why this,
    # and not Twilio's raw string, is half of the identity.
    status: str
    # Twilio's own word for it, kept so an operator reading the table can tell
    # an `undelivered` from a `failed` after the fact. Not part of the key.
    raw_status: str
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
