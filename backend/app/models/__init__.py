"""SQLModel table definitions.

Importing every model here means ``SQLModel.metadata`` is complete for Alembic
autogeneration and for test-database creation.
"""

from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.delivery_status_callback import DeliveryStatusCallback
from app.models.household import Household
from app.models.zone import Zone

__all__ = [
    "Alert",
    "AlertContent",
    "DeliveryAttempt",
    "DeliveryStatusCallback",
    "Household",
    "Zone",
]
