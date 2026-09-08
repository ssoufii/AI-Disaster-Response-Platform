"""Household table model.

Every downstream layer reads this profile: ``content_generator`` personalizes
against ``language``/``literacy_level``/``accessibility_needs``, and
``delivery_service`` routes on ``preferred_channel``/``fallback_channel_order``.

The enum-valued columns are stored as plain strings holding the enum *value*
(``"low_literacy"``, not ``"LOW_LITERACY"``). The API schemas in
``app/schemas/`` are the validation boundary; keeping the columns as strings
means adding a channel later is a code change, not a native-enum type rewrite.
"""

import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.enums import Channel, HouseholdStatus, LiteracyLevel


class Household(SQLModel, table=True):
    __tablename__ = "household"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    # Unique so registration and the seed script are both idempotent per household.
    phone_number: str = Field(unique=True, index=True)
    language: str = Field(default="en")
    literacy_level: str = Field(default=LiteracyLevel.STANDARD.value)
    accessibility_needs: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    preferred_channel: str = Field(default=Channel.SMS.value)
    fallback_channel_order: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    zone_id: uuid.UUID = Field(foreign_key="zone.id", index=True)
    last_known_status: str = Field(default=HouseholdStatus.UNKNOWN.value)
