"""Enumerations shared by models and schemas.

All of these are stored as plain strings in the database — keeping them out of
native database enum types means adding a value later is a code change, not a
migration with a type rewrite.
"""

from enum import StrEnum


class LiteracyLevel(StrEnum):
    STANDARD = "standard"
    LOW_LITERACY = "low_literacy"


class Channel(StrEnum):
    VOICE = "voice"
    SMS = "sms"
    VIDEO = "video"
    WHATSAPP = "whatsapp"


class HouseholdStatus(StrEnum):
    SAFE = "safe"
    UNREACHED = "unreached"
    UNKNOWN = "unknown"
