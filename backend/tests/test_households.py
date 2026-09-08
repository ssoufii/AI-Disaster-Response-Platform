"""Household registration."""

import uuid

from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.household import Household


async def test_register_household_returns_created_profile(client: AsyncClient) -> None:
    zone_id = (await client.post("/zones", json={"name": "Riverside"})).json()["id"]

    response = await client.post(
        "/households",
        json={
            "name": "Alvarez household",
            "phone_number": "+15550001111",
            "language": "es",
            "literacy_level": "low_literacy",
            "accessibility_needs": ["hard_of_hearing"],
            "preferred_channel": "voice",
            "fallback_channel_order": ["sms", "whatsapp"],
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    uuid.UUID(body["id"])
    assert body["zone_id"] == zone_id
    assert body["language"] == "es"
    assert body["literacy_level"] == "low_literacy"
    assert body["accessibility_needs"] == ["hard_of_hearing"]
    assert body["preferred_channel"] == "voice"
    assert body["fallback_channel_order"] == ["sms", "whatsapp"]
    assert body["last_known_status"] == "unknown"


async def test_register_household_applies_defaults(client: AsyncClient) -> None:
    zone_id = (await client.post("/zones", json={"name": "Riverside"})).json()["id"]

    response = await client.post(
        "/households",
        json={"name": "Minimal", "phone_number": "+15550009999", "zone_id": zone_id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["language"] == "en"
    assert body["literacy_level"] == "standard"
    assert body["preferred_channel"] == "sms"
    assert body["accessibility_needs"] == []
    assert body["fallback_channel_order"] == []


async def test_register_household_with_unknown_zone_creates_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        "/households",
        json={
            "name": "Orphan",
            "phone_number": "+15550004444",
            "zone_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 404
    remaining = (await session.exec(select(Household))).all()
    assert remaining == []


async def test_register_household_rejects_unknown_channel(client: AsyncClient) -> None:
    zone_id = (await client.post("/zones", json={"name": "Riverside"})).json()["id"]

    response = await client.post(
        "/households",
        json={
            "name": "Bad channel",
            "phone_number": "+15550005555",
            "preferred_channel": "carrier_pigeon",
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 422


async def test_register_household_rejects_unknown_literacy_level(client: AsyncClient) -> None:
    zone_id = (await client.post("/zones", json={"name": "Riverside"})).json()["id"]

    response = await client.post(
        "/households",
        json={
            "name": "Bad literacy",
            "phone_number": "+15550006666",
            "literacy_level": "somewhat",
            "zone_id": zone_id,
        },
    )

    assert response.status_code == 422
