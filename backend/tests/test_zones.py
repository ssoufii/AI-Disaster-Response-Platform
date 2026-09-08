"""Zone registration and household listing."""

import uuid

from httpx import AsyncClient


async def test_create_zone_returns_id(client: AsyncClient) -> None:
    response = await client.post("/zones", json={"name": "Riverside District"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Riverside District"
    uuid.UUID(body["id"])  # a real id was assigned


async def test_create_zone_accepts_geo_boundary(client: AsyncClient) -> None:
    boundary = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}

    response = await client.post("/zones", json={"name": "Coastal", "geo_boundary": boundary})

    assert response.status_code == 201
    assert response.json()["geo_boundary"] == boundary


async def test_create_zone_rejects_empty_name(client: AsyncClient) -> None:
    response = await client.post("/zones", json={"name": ""})

    assert response.status_code == 422


async def test_list_zone_households_returns_full_profiles(client: AsyncClient) -> None:
    zone_id = (await client.post("/zones", json={"name": "Riverside"})).json()["id"]
    profiles = [
        {
            "name": "Alvarez household",
            "phone_number": "+15550001111",
            "language": "es",
            "literacy_level": "low_literacy",
            "accessibility_needs": [],
            "preferred_channel": "voice",
            "fallback_channel_order": ["sms"],
            "zone_id": zone_id,
        },
        {
            "name": "Nguyen household",
            "phone_number": "+15550002222",
            "language": "en",
            "literacy_level": "standard",
            "accessibility_needs": ["asl", "hard_of_hearing"],
            "preferred_channel": "video",
            "fallback_channel_order": ["sms", "voice"],
            "zone_id": zone_id,
        },
    ]
    for profile in profiles:
        assert (await client.post("/households", json=profile)).status_code == 201

    response = await client.get(f"/zones/{zone_id}/households")

    assert response.status_code == 200
    listed = {h["phone_number"]: h for h in response.json()}
    assert len(listed) == 2
    for profile in profiles:
        household = listed[profile["phone_number"]]
        for field in (
            "language",
            "literacy_level",
            "accessibility_needs",
            "preferred_channel",
            "fallback_channel_order",
        ):
            assert household[field] == profile[field]
        assert household["last_known_status"] == "unknown"


async def test_list_households_excludes_other_zones(client: AsyncClient) -> None:
    zone_a = (await client.post("/zones", json={"name": "A"})).json()["id"]
    zone_b = (await client.post("/zones", json={"name": "B"})).json()["id"]
    await client.post(
        "/households",
        json={"name": "In A", "phone_number": "+15550003333", "zone_id": zone_a},
    )

    response = await client.get(f"/zones/{zone_b}/households")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_households_for_unknown_zone_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/zones/{uuid.uuid4()}/households")

    assert response.status_code == 404
