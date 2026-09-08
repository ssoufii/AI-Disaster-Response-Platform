"""Household routes."""

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.exceptions import ZoneNotFoundError
from app.models.household import Household
from app.models.zone import Zone
from app.schemas.household import HouseholdCreate, HouseholdRead

router = APIRouter(prefix="/households", tags=["households"])


@router.post("", response_model=HouseholdRead, status_code=status.HTTP_201_CREATED)
async def create_household(
    payload: HouseholdCreate, session: AsyncSession = Depends(get_session)
) -> HouseholdRead:
    # A household's zone must always be valid — every later story assumes it.
    zone = await session.get(Zone, payload.zone_id)
    if zone is None:
        raise ZoneNotFoundError(payload.zone_id)

    household = Household(
        name=payload.name,
        phone_number=payload.phone_number,
        language=payload.language,
        literacy_level=payload.literacy_level.value,
        accessibility_needs=payload.accessibility_needs,
        preferred_channel=payload.preferred_channel.value,
        fallback_channel_order=[c.value for c in payload.fallback_channel_order],
        zone_id=payload.zone_id,
    )
    session.add(household)
    await session.commit()
    await session.refresh(household)
    return HouseholdRead.model_validate(household, from_attributes=True)
