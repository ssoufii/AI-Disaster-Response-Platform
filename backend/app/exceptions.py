"""Domain exceptions.

Services and routes raise these; ``app.main`` translates them into HTTP
responses. Nothing below the API layer should know about ``HTTPException``.
"""


class DomainError(Exception):
    """Base class for every domain-level error."""


class NotFoundError(DomainError):
    """A referenced entity does not exist."""

    def __init__(self, entity: str, entity_id: object) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} not found")


class ZoneNotFoundError(NotFoundError):
    def __init__(self, zone_id: object) -> None:
        super().__init__("Zone", zone_id)


class AlertNotFoundError(NotFoundError):
    def __init__(self, alert_id: object) -> None:
        super().__init__("Alert", alert_id)


class ContentGenerationError(DomainError):
    """Claude returned something that is not valid alert content.

    Raised loudly rather than papered over: the caller decides whether to retry
    or fall back to a pre-written template.
    """
