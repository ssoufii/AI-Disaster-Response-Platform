"""Request/response schemas for households."""

import uuid

from pydantic import BaseModel, Field

from app.models.enums import Channel, HouseholdStatus, LiteracyLevel


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1)
    phone_number: str = Field(min_length=1)
    language: str = "en"
    literacy_level: LiteracyLevel = LiteracyLevel.STANDARD
    accessibility_needs: list[str] = Field(default_factory=list)
    preferred_channel: Channel = Channel.SMS
    fallback_channel_order: list[Channel] = Field(default_factory=list)
    zone_id: uuid.UUID


class HouseholdRead(BaseModel):
    id: uuid.UUID
    name: str
    phone_number: str
    language: str
    literacy_level: LiteracyLevel
    accessibility_needs: list[str]
    preferred_channel: Channel
    fallback_channel_order: list[Channel]
    zone_id: uuid.UUID
    last_known_status: HouseholdStatus
