"""Seed a demo zone and the household profiles every later story is tested against.

Run against a migrated database::

    uv run alembic upgrade head
    uv run python scripts/seed.py

CLAUDE.md's Testing section requires fixtures spanning low-literacy English,
Spanish voice, ASL/video, a non-English SMS household, and one number that
Twilio reliably fails — the last one is what makes fallback rerouting
provable rather than assumed.

The script is idempotent: households are keyed on ``phone_number`` (unique in
the schema) and the zone on its name, so re-running it after a schema or
profile change updates rows in place instead of duplicating them.
"""

import asyncio
from dataclasses import dataclass, field

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import async_session_factory
from app.logging_config import configure_logging
from app.models.enums import Channel, LiteracyLevel
from app.models.household import Household
from app.models.zone import Zone

log = structlog.get_logger(__name__)

SEED_ZONE_NAME = "Riverside Flood Plain"

# Twilio's magic test number for "this number cannot receive SMS" (error 21614).
# A documented magic number rather than an invented one, so the guaranteed-fail
# household behaves the same against a real Twilio test account as it does
# against the mocked client in tests.
TWILIO_UNDELIVERABLE_SMS_NUMBER = "+15005550009"


@dataclass(frozen=True)
class HouseholdSeed:
    """One demo household profile. Tuples keep the fixtures immutable."""

    name: str
    phone_number: str
    language: str
    literacy_level: LiteracyLevel
    preferred_channel: Channel
    fallback_channel_order: tuple[Channel, ...]
    accessibility_needs: tuple[str, ...] = field(default=())


HOUSEHOLD_SEEDS: tuple[HouseholdSeed, ...] = (
    HouseholdSeed(
        # Low-literacy English: exercises the short-sentence, <=160-char SMS path.
        name="Baker household",
        phone_number="+15550100001",
        language="en",
        literacy_level=LiteracyLevel.LOW_LITERACY,
        preferred_channel=Channel.SMS,
        fallback_channel_order=(Channel.VOICE,),
    ),
    HouseholdSeed(
        # Spanish voice: generation must produce a Spanish voice_script.
        name="Alvarez household",
        phone_number="+15550100002",
        language="es",
        literacy_level=LiteracyLevel.STANDARD,
        preferred_channel=Channel.VOICE,
        fallback_channel_order=(Channel.SMS,),
    ),
    HouseholdSeed(
        # ASL/video: the accessibility need that rules out both text and audio.
        name="Whitfield household",
        phone_number="+15550100003",
        language="en",
        literacy_level=LiteracyLevel.STANDARD,
        preferred_channel=Channel.VIDEO,
        fallback_channel_order=(Channel.WHATSAPP, Channel.SMS),
        accessibility_needs=("asl", "deaf"),
    ),
    HouseholdSeed(
        # Non-English SMS, in a language other than the Spanish voice household's,
        # so language and channel vary independently across the fixture set.
        name="Tran household",
        phone_number="+15550100004",
        language="vi",
        literacy_level=LiteracyLevel.STANDARD,
        preferred_channel=Channel.SMS,
        fallback_channel_order=(Channel.VOICE,),
    ),
    HouseholdSeed(
        # Guaranteed-fail SMS: the fixture the fallback chain is proven against.
        # Its fallback order is deliberately two deep so both a single reroute
        # and full exhaustion (household marked unreached) can be exercised.
        name="Okonkwo household",
        phone_number=TWILIO_UNDELIVERABLE_SMS_NUMBER,
        language="en",
        literacy_level=LiteracyLevel.STANDARD,
        preferred_channel=Channel.SMS,
        fallback_channel_order=(Channel.VOICE, Channel.WHATSAPP),
    ),
)


def redact(phone_number: str) -> str:
    """Last 4 digits only — full numbers are never logged (CLAUDE.md)."""
    return f"***{phone_number[-4:]}"


async def get_or_create_zone(session: AsyncSession, name: str) -> Zone:
    """Fetch the demo zone by name, creating it on the first run."""
    existing = (await session.exec(select(Zone).where(Zone.name == name))).first()
    if existing is not None:
        return existing

    zone = Zone(name=name)
    session.add(zone)
    await session.flush()
    return zone


async def upsert_household(session: AsyncSession, seed: HouseholdSeed, zone: Zone) -> Household:
    """Create the household, or update the existing row with the same number.

    ``last_known_status`` is left alone on an update: it is delivery state, not
    fixture data, and re-seeding should not erase what a dispatch recorded.
    """
    existing = (
        await session.exec(select(Household).where(Household.phone_number == seed.phone_number))
    ).first()

    household = existing if existing is not None else Household(phone_number=seed.phone_number)
    household.name = seed.name
    household.language = seed.language
    household.literacy_level = seed.literacy_level.value
    household.accessibility_needs = list(seed.accessibility_needs)
    household.preferred_channel = seed.preferred_channel.value
    household.fallback_channel_order = [c.value for c in seed.fallback_channel_order]
    household.zone_id = zone.id

    session.add(household)
    log.info(
        "seed.household_upserted",
        household_id=str(household.id),
        phone_number=redact(household.phone_number),
        preferred_channel=household.preferred_channel,
        created=existing is None,
    )
    return household


async def seed_demo_data(session: AsyncSession) -> tuple[Zone, list[Household]]:
    """Seed the demo zone and every household profile. Safe to run repeatedly."""
    zone = await get_or_create_zone(session, SEED_ZONE_NAME)
    households = [await upsert_household(session, seed, zone) for seed in HOUSEHOLD_SEEDS]
    await session.commit()
    return zone, households


async def main() -> None:
    configure_logging()
    async with async_session_factory() as session:
        zone, households = await seed_demo_data(session)
    log.info("seed.complete", zone_id=str(zone.id), household_count=len(households))


if __name__ == "__main__":
    asyncio.run(main())
