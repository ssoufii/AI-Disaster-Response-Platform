"""Zone routes."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.exceptions import ZoneNotFoundError
from app.models.household import Household
from app.models.zone import Zone
from app.schemas.household import HouseholdRead
from app.schemas.zone import ZoneCreate, ZoneRead

router = APIRouter(prefix="/zones", tags=["zones"])


@router.post("", response_model=ZoneRead, status_code=status.HTTP_201_CREATED)
async def create_zone(
    payload: ZoneCreate, session: AsyncSession = Depends(get_session)
) -> ZoneRead:
    zone = Zone(name=payload.name, geo_boundary=payload.geo_boundary)
    session.add(zone)
    await session.commit()
    await session.refresh(zone)
    return ZoneRead.model_validate(zone, from_attributes=True)


@router.get("/{zone_id}/households", response_model=list[HouseholdRead])
async def list_zone_households(
    zone_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[HouseholdRead]:
    zone = await session.get(Zone, zone_id)
    if zone is None:
        raise ZoneNotFoundError(zone_id)

    result = await session.exec(select(Household).where(Household.zone_id == zone_id))
    return [HouseholdRead.model_validate(h, from_attributes=True) for h in result.all()]
