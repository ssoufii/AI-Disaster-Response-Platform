"""The live simulation demo's clock: a mock alert every few minutes.

Only ever started in ``SIMULATION_MODE`` (see ``main.py``). It does the one
thing a public demo has nobody to do for it — press "dispatch" — and it does it
through the same ``dispatch.dispatch_alert`` the console's route calls.

This is **not** a polling loop for fallback, and adds none. Rerouting is still
triggered by the status webhook the moment a failure is reported (Domain
Rule 3); this module only decides when the *next alert* starts.

The alerts come from a fixed, hand-written pool. Nothing here composes a
shelter address or a road name (Domain Rule 1) — the facts are constants below,
exactly as a dispatcher would have typed them.

Once a day the database is wiped and re-seeded so it cannot grow without bound.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from alembic.config import Config
from sqlmodel import delete, select

from alembic import command
from app.config import settings
from app.db import session_scope
from app.models.alert import Alert
from app.models.alert_content import AlertContent
from app.models.delivery_attempt import DeliveryAttempt
from app.models.delivery_status_callback import DeliveryStatusCallback
from app.models.enums import Severity
from app.models.household import Household
from app.models.zone import Zone
from app.services import dispatch, twilio_simulator

logger = structlog.get_logger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]

CREATED_BY = "simulation-demo"


@dataclass(frozen=True)
class DemoAlert:
    """One hand-written mock alert, tied to the seeded zone it is for."""

    zone_name: str
    severity: Severity
    title: str
    raw_message: str
    facts: dict[str, Any] = field(default_factory=dict)


ALERT_POOL: tuple[DemoAlert, ...] = (
    DemoAlert(
        zone_name="Riverside Flood Plain",
        severity=Severity.EVACUATE_NOW,
        title="River levee breach — evacuate now",
        raw_message=(
            "The Mill Street levee has breached. Leave the Riverside flood plain now "
            "and go to the shelter at Lincoln High School."
        ),
        facts={
            "shelter": "Lincoln High School, 400 Oak Avenue",
            "evacuation_route": "Oak Avenue north to Highway 9",
            "road_closures": ["Mill Street", "River Road"],
        },
    ),
    DemoAlert(
        zone_name="Cedar Ridge Wildfire Corridor",
        severity=Severity.WARNING,
        title="Wildfire smoke — prepare to leave",
        raw_message=(
            "A wildfire is burning east of Cedar Ridge. Pack essentials and be ready "
            "to leave if an evacuation is ordered."
        ),
        facts={
            "shelter": "Cedar Ridge Community Center, 12 Summit Road",
            "evacuation_route": "Summit Road west to Route 20",
        },
    ),
    DemoAlert(
        zone_name="Harbor Point Coastal Zone",
        severity=Severity.ADVISORY,
        title="Boil water advisory",
        raw_message=(
            "A water main break has affected Harbor Point. Boil tap water for one "
            "minute before drinking until further notice."
        ),
        facts={"water_pickup": "Harbor Point Fire Station, 8 Pier Street"},
    ),
    DemoAlert(
        zone_name="Cedar Ridge Wildfire Corridor",
        severity=Severity.EVACUATE_NOW,
        title="Wildfire crossing Ridge Line — evacuate now",
        raw_message=(
            "The fire has crossed Ridge Line Road. Leave Cedar Ridge now and go to "
            "the shelter at the County Fairgrounds."
        ),
        facts={
            "shelter": "County Fairgrounds, 1500 Fair Lane",
            "evacuation_route": "Summit Road west to Route 20",
            "road_closures": ["Ridge Line Road", "Pine Hollow Road"],
        },
    ),
    DemoAlert(
        zone_name="Riverside Flood Plain",
        severity=Severity.WARNING,
        title="Flood warning — river rising",
        raw_message=(
            "The river is expected to reach flood stage tonight. Move vehicles and "
            "valuables to higher ground and be ready to leave."
        ),
        facts={
            "shelter": "Lincoln High School, 400 Oak Avenue",
            "expected_flood_stage": "10:00 PM",
        },
    ),
    DemoAlert(
        zone_name="Harbor Point Coastal Zone",
        severity=Severity.EVACUATE_NOW,
        title="Storm surge — evacuate now",
        raw_message=(
            "A storm surge is expected at Harbor Point within the hour. Leave the "
            "coastal zone now and go to the shelter at Bayview Middle School."
        ),
        facts={
            "shelter": "Bayview Middle School, 77 Hill Street",
            "evacuation_route": "Pier Street to Hill Street, heading inland",
            "road_closures": ["Seawall Drive"],
        },
    ),
)


_next_dispatch_at: datetime | None = None


def next_dispatch_at() -> datetime | None:
    """When the next mock alert is due, or ``None`` when no scheduler is running."""
    return _next_dispatch_at


def _run_migrations() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


async def prepare_database() -> None:
    """Bring the demo database to a usable state: migrated, and seeded if empty.

    Migrations run in a thread because Alembic's async env starts its own event
    loop. An already-seeded database is left alone, so a restart does not erase
    the dispatches a visitor is looking at.
    """
    await asyncio.to_thread(_run_migrations)
    async with session_scope() as session:
        if (await session.exec(select(Zone).limit(1))).first() is None:
            await _seed(session)


async def _seed(session: Any) -> None:
    from scripts.seed import seed_simulation_data  # a script, not an app module

    households = await seed_simulation_data(session)
    logger.info("demo_scheduler.seeded", households=households)


async def reset_database() -> None:
    """Wipe every row and re-seed. Children first, so no row is ever orphaned."""
    await twilio_simulator.get_simulator().cancel()
    async with session_scope() as session:
        for model in (
            DeliveryStatusCallback,
            DeliveryAttempt,
            AlertContent,
            Alert,
            Household,
            Zone,
        ):
            await session.exec(delete(model))
        await session.commit()
        await _seed(session)
    logger.info("demo_scheduler.reset")


async def dispatch_next(index: int) -> Alert | None:
    """Draft and dispatch the pool's ``index``-th alert (wrapping around)."""
    demo = ALERT_POOL[index % len(ALERT_POOL)]
    async with session_scope() as session:
        zone = (await session.exec(select(Zone).where(Zone.name == demo.zone_name))).first()
        if zone is None:
            logger.error("demo_scheduler.zone_missing", zone_name=demo.zone_name)
            return None

        alert = Alert(
            title=demo.title,
            raw_message=demo.raw_message,
            severity=demo.severity.value,
            facts=dict(demo.facts),
            zone_id=zone.id,
            created_by=CREATED_BY,
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)

        result = await dispatch.dispatch_alert(session, alert)
        logger.info(
            "demo_scheduler.dispatched",
            alert_id=str(alert.id),
            severity=alert.severity,
            households=result.households,
            deliveries_started=result.deliveries_started,
        )
        return alert


def _next_reset_after(now: datetime) -> datetime:
    reset = now.replace(hour=settings.DEMO_RESET_HOUR_UTC % 24, minute=0, second=0, microsecond=0)
    return reset if reset > now else reset + timedelta(days=1)


async def tick(index: int) -> int:
    """One beat of the demo: dispatch the next alert unless one is still going.

    Returns the index to use next time, so a skipped beat does not skip an alert.
    """
    simulator = twilio_simulator.get_simulator()
    if simulator.active_count() > 0:
        # The last dispatch is still reporting statuses. Starting another now
        # would have two alerts competing for attention on a console that
        # shows one.
        logger.info("demo_scheduler.skipped_busy", in_flight=simulator.active_count())
        return index
    await dispatch_next(index)
    return index + 1


async def run_forever() -> None:
    """Dispatch a mock alert every interval, one at a time, until cancelled."""
    global _next_dispatch_at
    interval = timedelta(minutes=settings.DEMO_DISPATCH_INTERVAL_MINUTES)
    next_reset = _next_reset_after(datetime.now(UTC))
    index = 0

    while True:
        try:
            if datetime.now(UTC) >= next_reset:
                await reset_database()
                next_reset = _next_reset_after(datetime.now(UTC))
            index = await tick(index)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A demo that stops forever on one bad tick is worse than one that
            # skips a beat; the next tick tries again.
            logger.exception("demo_scheduler.tick_failed")

        _next_dispatch_at = datetime.now(UTC) + interval
        await asyncio.sleep(interval.total_seconds())
