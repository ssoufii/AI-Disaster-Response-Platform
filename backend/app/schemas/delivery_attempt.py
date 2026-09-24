"""Request/response schemas for delivery attempts."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import Channel, DeliveryStatus, HouseholdStatus


class DeliveryAttemptRead(BaseModel):
    """One attempt, exactly as the audit trail recorded it."""

    id: uuid.UUID
    channel: Channel
    attempt_number: int
    status: DeliveryStatus
    twilio_sid: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
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
