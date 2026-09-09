"""Alert routes."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.exceptions import AlertNotFoundError, ZoneNotFoundError
from app.models.alert import Alert
from app.models.zone import Zone
from app.schemas.alert import AlertCreate, AlertRead

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("", response_model=AlertRead, status_code=status.HTTP_201_CREATED)
async def create_alert(
    payload: AlertCreate, session: AsyncSession = Depends(get_session)
) -> AlertRead:
    # An alert's zone must exist — dispatch fans out over the zone's households.
    zone = await session.get(Zone, payload.zone_id)
    if zone is None:
        raise ZoneNotFoundError(payload.zone_id)

    alert = Alert(
        title=payload.title,
        raw_message=payload.raw_message,
        severity=payload.severity.value,
        zone_id=payload.zone_id,
        created_by=payload.created_by,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return AlertRead.model_validate(alert, from_attributes=True)


@router.get("/{alert_id}", response_model=AlertRead)
async def get_alert(alert_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> AlertRead:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError(alert_id)

    return AlertRead.model_validate(alert, from_attributes=True)
