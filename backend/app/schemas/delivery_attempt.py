"""Request/response schemas for delivery attempts."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel

from app.models.enums import Channel, DeliveryStatus, HouseholdStatus


def _as_utc(value: datetime) -> datetime:
    """Label a timezone-less datetime as the UTC it already is.

    Every timestamp in this system is written in UTC. PostgreSQL hands the
    offset back; SQLite (the simulation demo's database) drops it, and a console
    given a bare timestamp would read it as the viewer's local time.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class DeliveryAttemptRead(BaseModel):
    """One attempt, exactly as the audit trail recorded it."""

    id: uuid.UUID
    channel: Channel
    attempt_number: int
    status: DeliveryStatus
    twilio_sid: str | None = None
    started_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    error_reason: str | None = None


class HouseholdDeliveryStatus(BaseModel):
    """Where one household stands on one alert.

    ``current_attempt`` is the household's latest attempt, or null when it has
    none — a household whose channel is not yet wired up, or one that joined the
    zone after the dispatch. Null is deliberately distinguishable from a failure:
    the console must be able to show "not attempted" as its own state.
    """

    household_id: uuid.UUID
    name: str
    preferred_channel: Channel
    last_known_status: HouseholdStatus
    current_attempt: DeliveryAttemptRead | None = None


class TwilioStatusAck(BaseModel):
    """What the status webhook did with a callback.

    ``result`` is ``applied`` when the attempt was updated, ``duplicate`` when
    Twilio had already reported this state and the callback changed nothing, and
    ``ignored`` when the callback was not applied to an attempt — because it
    could not be placed at all, or because the attempt is already
    ``confirmed_received`` and a delivery status may never be written over a
    household's own word (Domain Rule 6).

    Always returned with a 200: Twilio retries anything else, and neither a
    callback we cannot place nor one we have already applied is helped by a
    retry. ``reason`` says why an ignored callback was ignored, so the endpoint
    is debuggable from the outside during an incident.
    """

    result: str
    status: DeliveryStatus | None = None
    reason: str | None = None
