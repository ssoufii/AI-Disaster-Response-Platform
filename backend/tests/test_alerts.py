"""Alert drafting.

Drafting involves no external calls — no Claude, no Twilio — so nothing needs
mocking here beyond the in-memory database the shared fixtures provide.
"""

import uuid

import pytest
from httpx import AsyncClient


async def _create_zone(client: AsyncClient, name: str = "Riverside District") -> str:
    return (await client.post("/zones", json={"name": name})).json()["id"]


async def test_create_alert_starts_as_draft(client: AsyncClient) -> None:
    zone_id = await _create_zone(client)

    response = await client.post(
        "/alerts",
        json={
            "title": "Flash flood warning",
            "raw_message": "Evacuate to Lincoln High School, 400 Oak St, before 6pm.",
            "severity": "warning",
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["severity"] == "warning"
    assert body["zone_id"] == zone_id
    uuid.UUID(body["id"])  # a real id was assigned


@pytest.mark.parametrize("severity", ["advisory", "warning", "evacuate_now"])
async def test_create_alert_accepts_each_severity(client: AsyncClient, severity: str) -> None:
    zone_id = await _create_zone(client)

    response = await client.post(
        "/alerts",
        json={
            "title": "Alert",
            "raw_message": "Stay indoors.",
            "severity": severity,
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 201
    assert response.json()["severity"] == severity


@pytest.mark.parametrize("severity", ["urgent", "EVACUATE_NOW", "", "critical"])
async def test_create_alert_rejects_unknown_severity(client: AsyncClient, severity: str) -> None:
    zone_id = await _create_zone(client)

    response = await client.post(
        "/alerts",
        json={
            "title": "Alert",
            "raw_message": "Stay indoors.",
            "severity": severity,
            "zone_id": zone_id,
        },
    )

    # Rejected at the schema boundary, so the handler never ran and no row exists.
    assert response.status_code == 422
    assert "id" not in response.json()


async def test_create_alert_rejects_empty_raw_message(client: AsyncClient) -> None:
    zone_id = await _create_zone(client)

    response = await client.post(
        "/alerts",
        json={"title": "Alert", "raw_message": "", "severity": "advisory", "zone_id": zone_id},
    )

    assert response.status_code == 422


async def test_create_alert_for_unknown_zone_is_404(client: AsyncClient) -> None:
    response = await client.post(
        "/alerts",
        json={
            "title": "Alert",
            "raw_message": "Stay indoors.",
            "severity": "advisory",
            "zone_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 404


async def test_get_alert_returns_full_draft(client: AsyncClient) -> None:
    zone_id = await _create_zone(client)
    created = (
        await client.post(
            "/alerts",
            json={
                "title": "Wildfire evacuation",
                "raw_message": "Leave now via Route 12. Shelter: Lincoln High School.",
                "severity": "evacuate_now",
                "zone_id": zone_id,
                "created_by": "dispatcher-7",
            },
        )
    ).json()

    response = await client.get(f"/alerts/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["raw_message"] == "Leave now via Route 12. Shelter: Lincoln High School."
    assert body["severity"] == "evacuate_now"
    assert body["zone_id"] == zone_id
    assert body["status"] == "draft"
    assert body["created_by"] == "dispatcher-7"
    assert body["created_at"]


async def test_alert_round_trips_its_structured_facts(client: AsyncClient) -> None:
    zone_id = await _create_zone(client)
    facts = {
        "shelter": "Lincoln High School, 400 Oak St",
        "routes": ["Route 12 west"],
        "leave_by": "18:00",
    }

    created = (
        await client.post(
            "/alerts",
            json={
                "title": "Wildfire evacuation",
                "raw_message": "Leave the canyon now.",
                "severity": "evacuate_now",
                "facts": facts,
                "zone_id": zone_id,
            },
        )
    ).json()

    # Content generation copies these verbatim, so they must survive the round
    # trip exactly as the dispatcher typed them.
    assert created["facts"] == facts
    assert (await client.get(f"/alerts/{created['id']}")).json()["facts"] == facts


async def test_alert_without_facts_defaults_to_an_empty_object(client: AsyncClient) -> None:
    zone_id = await _create_zone(client)

    response = await client.post(
        "/alerts",
        json={
            "title": "Air quality advisory",
            "raw_message": "Stay indoors.",
            "severity": "advisory",
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 201
    assert response.json()["facts"] == {}


async def test_get_unknown_alert_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/alerts/{uuid.uuid4()}")

    assert response.status_code == 404
