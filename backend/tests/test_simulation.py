"""The live simulation demo (Epic 9, #42–#46).

What these prove, in one line each:

- In simulation mode no real Twilio or Anthropic client is ever built.
- The simulator drives the *real* webhooks with validly signed callbacks, so a
  simulated failure reroutes and a simulated exhaustion marks ``unreached``.
- ``confirmed_received`` still only comes from a keypress (Domain Rule 6).
- The demo is public to read and closed to change.
- The scheduler dispatches one alert at a time and can wipe and re-seed.
"""

import asyncio
import os
import random
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings, settings
from app.models.alert import Alert
from app.models.delivery_attempt import DeliveryAttempt
from app.models.enums import AlertStatus, Channel, DeliveryStatus, HouseholdStatus, LiteracyLevel
from app.models.household import Household
from app.models.zone import Zone
from app.services import (
    content_generator,
    delivery_service,
    demo_scheduler,
    dispatcher_ws,
    twilio_simulator,
)
from app.services.twilio_simulator import UNREACHABLE_PREFIX, SimulatedTwilio
from scripts.seed import (
    SIMULATION_HOUSEHOLDS_PER_ZONE,
    SIMULATION_ZONE_NAMES,
    seed_demo_data,
    seed_simulation_data,
    simulation_seeds,
)

# Captured at import, before conftest's autouse fixture swaps in the fake client.
REAL_GET_CLIENT = delivery_service.get_client

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def simulation(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> SimulatedTwilio:
    """Simulation mode with a deterministic simulator and no waiting.

    Failure rates are zeroed and the confirm rate is 1, so the only failures are
    the unreachable numbers and every answered call presses 1 — outcomes a test
    can assert exactly.

    Every test shares one database session, which cannot be used by two tasks at
    once. So the simulator is held at its first pause until the test releases it
    (after the dispatch request has finished), and its callbacks are delivered
    one at a time.
    """
    monkeypatch.setattr(settings, "SIMULATION_MODE", True)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "simulation-only-token")
    monkeypatch.setattr(settings, "ASL_CLIP_BASE_URL", "https://clips.invalid/asl")
    monkeypatch.setattr(settings, "SIM_SMS_FAILURE_RATE", 0.0)
    monkeypatch.setattr(settings, "SIM_VOICE_NO_ANSWER_RATE", 0.0)
    monkeypatch.setattr(settings, "SIM_CLIP_FAILURE_RATE", 0.0)
    monkeypatch.setattr(settings, "SIM_VOICE_CONFIRM_RATE", 1.0)

    sim = SimulatedTwilio(rng=random.Random(7))
    sim.release = asyncio.Event()  # type: ignore[attr-defined]

    async def gated_pause() -> None:
        await sim.release.wait()  # type: ignore[attr-defined]

    monkeypatch.setattr(sim, "_pause", gated_pause)

    one_at_a_time = asyncio.Lock()
    real_post = twilio_simulator._post

    async def serial_post(url: str, params: dict[str, str]) -> None:
        async with one_at_a_time:
            await real_post(url, params)

    monkeypatch.setattr(twilio_simulator, "_post", serial_post)
    monkeypatch.setattr(twilio_simulator, "_simulator", sim)
    monkeypatch.setattr(delivery_service, "get_client", REAL_GET_CLIENT)

    @asynccontextmanager
    async def test_session_scope() -> AsyncGenerator[AsyncSession, None]:
        yield session

    monkeypatch.setattr(demo_scheduler, "session_scope", test_session_scope)
    dispatcher_ws.manager = dispatcher_ws.ConnectionManager()
    return sim


async def _finish(sim: SimulatedTwilio) -> None:
    sim.release.set()  # type: ignore[attr-defined]
    await sim.drain()


async def _dispatch_seed_zone(client: AsyncClient, session: AsyncSession) -> tuple[str, Zone]:
    zone, _ = await seed_demo_data(session)
    created = await client.post(
        "/alerts",
        json={
            "title": "Levee breach",
            "raw_message": "Leave now.",
            "severity": "evacuate_now",
            "zone_id": str(zone.id),
        },
    )
    alert_id = created.json()["id"]
    dispatched = await client.post(f"/alerts/{alert_id}/dispatch")
    assert dispatched.status_code == 200
    return alert_id, zone


# --- #42: the simulator stands in for Twilio --------------------------------


def test_simulation_mode_never_builds_a_real_twilio_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a real Twilio client was constructed in simulation mode")

    monkeypatch.setattr(settings, "SIMULATION_MODE", True)
    monkeypatch.setattr(delivery_service, "Client", refuse)

    assert isinstance(REAL_GET_CLIENT(), SimulatedTwilio)


def test_simulation_mode_replaces_the_auth_token_with_a_private_one() -> None:
    """Only this process's simulator can sign a callback, whatever was configured."""
    first = Settings(SIMULATION_MODE=True, TWILIO_AUTH_TOKEN="a-real-token")
    second = Settings(SIMULATION_MODE=True, TWILIO_AUTH_TOKEN="a-real-token")

    assert first.TWILIO_AUTH_TOKEN not in ("", "a-real-token")
    assert first.TWILIO_AUTH_TOKEN != second.TWILIO_AUTH_TOKEN
    assert Settings(SIMULATION_MODE=False, TWILIO_AUTH_TOKEN="kept").TWILIO_AUTH_TOKEN == "kept"


async def test_a_simulated_dispatch_runs_the_real_delivery_path(
    client: AsyncClient, session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    alert_id, _ = await _dispatch_seed_zone(client, session)
    await _finish(simulation)

    snapshot = (await client.get(f"/alerts/{alert_id}/status")).json()
    by_name = {h["name"]: h for h in snapshot["households"]}

    # SMS households: delivered on the first attempt, and not "confirmed".
    assert by_name["Baker household"]["current_attempt"]["status"] == "delivered"
    assert by_name["Baker household"]["current_attempt"]["attempt_number"] == 1
    # A voice household that pressed 1: the only route to confirmed_received.
    assert by_name["Alvarez household"]["current_attempt"]["status"] == "confirmed_received"
    # ASL clip sends go through the same path.
    assert by_name["Whitfield household"]["current_attempt"]["status"] == "delivered"


async def test_an_unreachable_household_walks_its_chain_to_unreached(
    client: AsyncClient, session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    alert_id, _ = await _dispatch_seed_zone(client, session)
    await _finish(simulation)

    household = (
        await session.exec(select(Household).where(Household.name == "Okonkwo household"))
    ).one()
    attempts = (
        await session.exec(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.household_id == household.id)
            .order_by(DeliveryAttempt.attempt_number)
        )
    ).all()

    # One row per channel tried, never a mutated retry (Domain Rule 2).
    assert [(a.attempt_number, a.channel) for a in attempts] == [
        (1, "sms"),
        (2, "voice"),
        (3, "whatsapp"),
    ]
    assert {a.status for a in attempts} <= {
        DeliveryStatus.FAILED.value,
        DeliveryStatus.NO_ANSWER.value,
    }
    await session.refresh(household)
    assert household.last_known_status == HouseholdStatus.UNREACHED.value
    assert alert_id


async def test_a_call_nobody_confirms_is_delivered_not_confirmed(
    client: AsyncClient,
    session: AsyncSession,
    simulation: SimulatedTwilio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SIM_VOICE_CONFIRM_RATE", 0.0)
    alert_id, _ = await _dispatch_seed_zone(client, session)
    await _finish(simulation)

    snapshot = (await client.get(f"/alerts/{alert_id}/status")).json()
    statuses = {h["current_attempt"]["status"] for h in snapshot["households"]}

    assert "confirmed_received" not in statuses


async def test_the_webhook_still_refuses_an_unsigned_callback_in_simulation_mode(
    anonymous_client: AsyncClient, simulation: SimulatedTwilio
) -> None:
    response = await anonymous_client.post(
        "/webhooks/twilio/status",
        data={"MessageSid": "SM123", "MessageStatus": "delivered"},
        headers={"X-Twilio-Signature": "forged"},
    )

    assert response.status_code == 403


# --- #43: content without Claude --------------------------------------------


async def test_simulation_mode_uses_templates_and_never_calls_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> None:
        raise AssertionError("the Anthropic client was requested in simulation mode")

    monkeypatch.setattr(settings, "SIMULATION_MODE", True)
    monkeypatch.setattr(content_generator, "get_client", refuse)
    alert = Alert(
        title="t", raw_message="m", severity="evacuate_now", facts={}, zone_id=uuid.uuid4()
    )
    household = Household(
        name="h", phone_number="+15550100001", language="es", zone_id=alert.zone_id
    )

    content = await content_generator.generate(alert, household)

    assert content.language_used == "es"
    assert content.sms_text


# --- #44: SQLite and seed data ----------------------------------------------


def test_migrations_apply_cleanly_on_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert database.exists()


def test_simulation_seeds_cover_three_zones_and_every_required_profile() -> None:
    zones = simulation_seeds()
    everyone = [seed for seeds in zones.values() for seed in seeds]

    assert tuple(zones) == SIMULATION_ZONE_NAMES
    assert all(len(seeds) == SIMULATION_HOUSEHOLDS_PER_ZONE for seeds in zones.values())
    assert len({seed.phone_number for seed in everyone}) == len(everyone)
    # Every zone has a household that can never be reached.
    for seeds in zones.values():
        assert any(seed.phone_number.startswith(UNREACHABLE_PREFIX) for seed in seeds)
    # The profiles CLAUDE.md requires of seed data.
    assert any(
        s.language == "en" and s.literacy_level is LiteracyLevel.LOW_LITERACY for s in everyone
    )
    assert any(s.language == "es" and s.preferred_channel is Channel.VOICE for s in everyone)
    assert any("asl" in s.accessibility_needs for s in everyone)
    assert any(s.language == "vi" and s.preferred_channel is Channel.SMS for s in everyone)


async def test_seeding_simulation_data_twice_does_not_duplicate(session: AsyncSession) -> None:
    await seed_simulation_data(session)
    await seed_simulation_data(session)

    households = (await session.exec(select(Household))).all()
    zones = (await session.exec(select(Zone))).all()

    assert len(households) == len(SIMULATION_ZONE_NAMES) * SIMULATION_HOUSEHOLDS_PER_ZONE
    assert len(zones) == len(SIMULATION_ZONE_NAMES)


# --- #45: the scheduler ------------------------------------------------------


def test_every_pool_alert_is_for_a_seeded_zone() -> None:
    assert {demo.zone_name for demo in demo_scheduler.ALERT_POOL} <= set(SIMULATION_ZONE_NAMES)


async def test_a_tick_dispatches_the_next_pool_alert(
    session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    await seed_simulation_data(session)

    next_index = await demo_scheduler.tick(0)

    alert = (await session.exec(select(Alert))).one()
    attempts = (await session.exec(select(DeliveryAttempt))).all()
    assert next_index == 1
    assert alert.title == demo_scheduler.ALERT_POOL[0].title
    assert alert.facts == demo_scheduler.ALERT_POOL[0].facts
    assert alert.status == AlertStatus.DISPATCHING.value
    assert len(attempts) == SIMULATION_HOUSEHOLDS_PER_ZONE
    await simulation.cancel()


async def test_a_tick_does_not_start_a_second_dispatch_while_one_is_running(
    session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    await seed_simulation_data(session)
    await demo_scheduler.tick(0)
    assert simulation.active_count() > 0

    next_index = await demo_scheduler.tick(1)

    assert next_index == 1
    assert len((await session.exec(select(Alert))).all()) == 1
    await simulation.cancel()


async def test_reset_wipes_dispatches_and_reseeds(
    session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    await seed_simulation_data(session)
    await demo_scheduler.tick(0)

    await demo_scheduler.reset_database()

    assert (await session.exec(select(Alert))).all() == []
    assert (await session.exec(select(DeliveryAttempt))).all() == []
    assert len((await session.exec(select(Household))).all()) == 60
    assert simulation.active_count() == 0


def test_the_scheduler_only_starts_in_simulation_mode() -> None:
    """Read off the field: the switch is off unless a deployment turns it on."""
    assert Settings.model_fields["SIMULATION_MODE"].default is False


# --- #46: public to read, closed to change ----------------------------------


async def test_latest_alert_is_the_newest_dispatched_one(
    client: AsyncClient, session: AsyncSession, simulation: SimulatedTwilio
) -> None:
    assert (await client.get("/alerts/latest")).status_code == 404

    alert_id, zone = await _dispatch_seed_zone(client, session)
    # A newer draft must not displace it: a draft has nothing to watch.
    await client.post(
        "/alerts",
        json={
            "title": "Draft",
            "raw_message": "x",
            "severity": "advisory",
            "zone_id": str(zone.id),
        },
    )

    latest = (await client.get("/alerts/latest")).json()

    assert latest["id"] == alert_id
    assert latest["zone_name"] == zone.name
    assert latest["created_at"].endswith(("Z", "+00:00"))
    await simulation.cancel()


async def test_the_demo_console_is_readable_without_a_token(
    client: AsyncClient,
    anonymous_client: AsyncClient,
    session: AsyncSession,
    simulation: SimulatedTwilio,
) -> None:
    alert_id, _ = await _dispatch_seed_zone(client, session)

    assert (await anonymous_client.get("/alerts/latest")).status_code == 200
    assert (await anonymous_client.get(f"/alerts/{alert_id}/status")).status_code == 200
    await simulation.cancel()


async def test_nothing_can_be_changed_without_the_token_in_simulation_mode(
    client: AsyncClient,
    anonymous_client: AsyncClient,
    session: AsyncSession,
    simulation: SimulatedTwilio,
) -> None:
    alert_id, zone = await _dispatch_seed_zone(client, session)
    await simulation.cancel()
    draft = {"title": "t", "raw_message": "m", "severity": "advisory", "zone_id": str(zone.id)}
    household = {"name": "n", "phone_number": "+15550109999", "zone_id": str(zone.id)}

    refused = [
        await anonymous_client.post("/alerts", json=draft),
        await anonymous_client.post(f"/alerts/{alert_id}/dispatch"),
        await anonymous_client.post("/zones", json={"name": "z"}),
        await anonymous_client.post("/households", json=household),
        # Reads that are not the console's two stay closed as well: the
        # household register is not part of watching the demo.
        await anonymous_client.get(f"/alerts/{alert_id}"),
        await anonymous_client.get(f"/zones/{zone.id}/households"),
    ]

    assert [response.status_code for response in refused] == [401] * len(refused)


async def test_public_reads_are_closed_when_simulation_mode_is_off(
    anonymous_client: AsyncClient,
) -> None:
    assert (await anonymous_client.get("/alerts/latest")).status_code == 401
    assert (await anonymous_client.get(f"/alerts/{uuid.uuid4()}/status")).status_code == 401


class _Socket:
    """The two things the endpoint does to a socket before subscribing it."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    async def receive_text(self) -> str:
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect()


async def test_the_demo_socket_accepts_a_viewer_without_a_token(
    simulation: SimulatedTwilio,
) -> None:
    websocket = _Socket()

    await dispatcher_ws.alert_updates(websocket, uuid.uuid4(), token=None)  # type: ignore[arg-type]

    assert websocket.accepted
    assert websocket.closed_with is None
