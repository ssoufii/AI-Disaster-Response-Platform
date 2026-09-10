"""Seed script fixtures and idempotency.

The script talks to a session, so these run it against the same in-memory
database the API tests use. No Twilio or Anthropic client is involved.
"""

from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.enums import Channel, HouseholdStatus, LiteracyLevel
from app.models.household import Household
from app.models.zone import Zone
from scripts.seed import (
    HOUSEHOLD_SEEDS,
    SEED_ZONE_NAME,
    TWILIO_UNDELIVERABLE_SMS_NUMBER,
    redact,
    seed_demo_data,
)


async def test_seed_creates_zone_and_every_profile(session: AsyncSession) -> None:
    zone, households = await seed_demo_data(session)

    assert zone.name == SEED_ZONE_NAME
    assert len(households) >= 5

    profiles = {h.phone_number: h for h in (await session.exec(select(Household))).all()}
    assert len(profiles) == len(HOUSEHOLD_SEEDS)
    assert all(h.zone_id == zone.id for h in profiles.values())

    # The five profiles CLAUDE.md's Testing section requires.
    assert any(
        h.language == "en"
        and h.literacy_level == LiteracyLevel.LOW_LITERACY
        and h.preferred_channel == Channel.SMS
        for h in profiles.values()
    )
    assert any(
        h.language == "es" and h.preferred_channel == Channel.VOICE for h in profiles.values()
    )
    assert any(
        "asl" in h.accessibility_needs and h.preferred_channel == Channel.VIDEO
        for h in profiles.values()
    )
    assert any(
        h.language not in ("en", "es") and h.preferred_channel == Channel.SMS
        for h in profiles.values()
    )
    assert TWILIO_UNDELIVERABLE_SMS_NUMBER in profiles


async def test_guaranteed_fail_household_can_exercise_the_fallback_chain(
    session: AsyncSession,
) -> None:
    await seed_demo_data(session)

    household = (
        await session.exec(
            select(Household).where(Household.phone_number == TWILIO_UNDELIVERABLE_SMS_NUMBER)
        )
    ).one()

    assert household.preferred_channel == Channel.SMS
    assert household.fallback_channel_order
    assert household.preferred_channel not in household.fallback_channel_order


async def test_every_seeded_household_has_a_fallback_channel(session: AsyncSession) -> None:
    _, households = await seed_demo_data(session)

    for household in households:
        assert household.fallback_channel_order, household.name
        assert household.preferred_channel not in household.fallback_channel_order


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    first_zone, _ = await seed_demo_data(session)
    second_zone, _ = await seed_demo_data(session)

    assert second_zone.id == first_zone.id
    assert len((await session.exec(select(Zone))).all()) == 1
    assert len((await session.exec(select(Household))).all()) == len(HOUSEHOLD_SEEDS)


async def test_reseeding_preserves_recorded_delivery_state(session: AsyncSession) -> None:
    _, households = await seed_demo_data(session)
    household = households[0]
    household.last_known_status = HouseholdStatus.UNREACHED.value
    session.add(household)
    await session.commit()

    await seed_demo_data(session)

    refreshed = (
        await session.exec(
            select(Household).where(Household.phone_number == household.phone_number)
        )
    ).one()
    assert refreshed.last_known_status == HouseholdStatus.UNREACHED


async def test_seeded_profiles_are_visible_through_the_zone_endpoint(
    session: AsyncSession, client: AsyncClient
) -> None:
    zone, _ = await seed_demo_data(session)

    response = await client.get(f"/zones/{zone.id}/households")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(HOUSEHOLD_SEEDS)
    by_phone = {h["phone_number"]: h for h in body}
    for seed in HOUSEHOLD_SEEDS:
        rendered = by_phone[seed.phone_number]
        assert rendered["language"] == seed.language
        assert rendered["literacy_level"] == seed.literacy_level
        assert rendered["accessibility_needs"] == list(seed.accessibility_needs)
        assert rendered["preferred_channel"] == seed.preferred_channel
        assert rendered["fallback_channel_order"] == list(seed.fallback_channel_order)


def test_redact_keeps_only_the_last_four_digits() -> None:
    assert redact("+15550100001") == "***0001"
    assert "5550100001" not in redact("+15550100001")
