"""Alert routes."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.exceptions import AlertNotFoundError, ZoneNotFoundError
from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.enums import AlertStatus
from app.models.household import Household
from app.models.zone import Zone
from app.schemas.alert import AlertCreate, AlertDispatchRead, AlertRead
from app.services import content_generator

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
        facts=payload.facts,
        zone_id=payload.zone_id,
        created_by=payload.created_by,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return AlertRead.model_validate(alert, from_attributes=True)


@router.post("/{alert_id}/dispatch", response_model=AlertDispatchRead)
async def dispatch_alert(
    alert_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AlertDispatchRead:
    """Run the content-generation phase of a dispatch over the alert's zone.

    Generation comes first: delivery has nothing to send until every household
    in the zone has its own AlertContent row. Households that already have
    content for this alert are left alone, so re-dispatching never gives a
    household a second row.
    """
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError(alert_id)

    households = (
        await session.exec(select(Household).where(Household.zone_id == alert.zone_id))
    ).all()
    already_generated = set(
        (
            await session.exec(
                select(AlertContent.household_id).where(AlertContent.alert_id == alert.id)
            )
        ).all()
    )
    pending = [h for h in households if h.id not in already_generated]

    contents = await content_generator.generate_for_zone(alert, pending)
    for household, content in zip(pending, contents, strict=True):
        session.add(
            AlertContent(
                alert_id=alert.id,
                household_id=household.id,
                channel=household.preferred_channel,
                generated_text=content.sms_text,
                generated_script=content.voice_script,
                video_caption_text=content.asl_video_caption,
                language=content.language_used,
            )
        )

    alert.status = AlertStatus.DISPATCHING.value
    session.add(alert)
    await session.commit()

    return AlertDispatchRead(
        alert_id=alert.id,
        status=AlertStatus(alert.status),
        households=len(households),
        content_generated=len(pending),
    )


@router.get("/{alert_id}", response_model=AlertRead)
async def get_alert(alert_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> AlertRead:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError(alert_id)

    return AlertRead.model_validate(alert, from_attributes=True)
