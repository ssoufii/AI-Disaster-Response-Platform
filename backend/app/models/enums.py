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


class Severity(StrEnum):
    """Severity gates behavior, not just wording (CLAUDE.md, Domain Rule 5).

    ``evacuate_now`` skips any batching or rate-limit delay lower severities use.
    """

    ADVISORY = "advisory"
    WARNING = "warning"
    EVACUATE_NOW = "evacuate_now"


class AlertStatus(StrEnum):
    DRAFT = "draft"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
