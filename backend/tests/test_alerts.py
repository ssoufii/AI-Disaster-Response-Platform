"""Alert drafting and the dispatch endpoint's content-generation phase.

Drafting involves no external calls — no Claude, no Twilio. Dispatch does call
Claude, so every dispatch test replaces the Anthropic client wholesale: no test
here can reach the network (CLAUDE.md, Testing).
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.alert_content import AlertContent
from app.services import content_generator

GENERATED = {
    "sms_text": "Evacuate now. Go to Lincoln High School, 400 Oak St.",
    "voice_script": "Evacuate now. Go to Lincoln High School, 400 Oak St. Press 1 if you are safe.",
    "asl_video_caption": "Evacuate now.\nGo to Lincoln High School, 400 Oak St.",
    "language_used": "en",
}


def _fake_claude(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Install a stand-in Anthropic client; returns the list of prompts it saw.

    Content comes back in whatever language was requested, so a test can tell
    one household's row from another's.
    """
    seen: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        sent = json.loads(kwargs["messages"][0]["content"])
        seen.append(sent)
        payload = json.dumps({**GENERATED, "language_used": sent["target_language"]})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)])

    monkeypatch.setattr(
        content_generator,
        "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    return seen


async def _create_zone(client: AsyncClient, name: str = "Riverside District") -> str:
    return (await client.post("/zones", json={"name": name})).json()["id"]


async def _create_alert(client: AsyncClient, zone_id: str, **overrides: object) -> str:
    payload: dict[str, object] = {
        "title": "Flash flood evacuation",
        "raw_message": "Evacuate the riverside area immediately.",
        "severity": "evacuate_now",
        "facts": {"shelter": "Lincoln High School, 400 Oak St"},
        "zone_id": zone_id,
    }
    return (await client.post("/alerts", json=payload | overrides)).json()["id"]


async def _create_household(
    client: AsyncClient, zone_id: str, phone_number: str, **overrides: object
) -> str:
    payload: dict[str, object] = {
        "name": "Household",
        "phone_number": phone_number,
        "language": "en",
        "preferred_channel": "sms",
        "fallback_channel_order": ["voice"],
        "zone_id": zone_id,
    }
    return (await client.post("/households", json=payload | overrides)).json()["id"]


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


# --- Dispatch: the content-generation phase (issue #6) ----------------------
#
# Dispatch generates before it delivers: every household in the zone ends up
# with exactly one AlertContent row, produced by the bounded fan-out.


async def test_dispatch_generates_one_content_row_per_household_in_the_zone(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    alert_id = await _create_alert(client, zone_id)
    for i, language in enumerate(["en", "es", "vi"]):
        await _create_household(client, zone_id, f"+1555559{i:04d}", language=language)

    response = await client.post(f"/alerts/{alert_id}/dispatch")

    assert response.status_code == 200
    body = response.json()
    assert body["households"] == 3
    assert body["content_generated"] == 3

    rows = (
        await session.exec(select(AlertContent).where(AlertContent.alert_id == uuid.UUID(alert_id)))
    ).all()
    assert len(rows) == 3
    assert sorted(row.language for row in rows) == ["en", "es", "vi"]
    # The generated fields land on the columns the data model names for them.
    assert all(row.generated_text == GENERATED["sms_text"] for row in rows)
    assert all(row.generated_script == GENERATED["voice_script"] for row in rows)
    assert all(row.video_caption_text == GENERATED["asl_video_caption"] for row in rows)
    assert all(row.channel == "sms" for row in rows)


async def test_dispatch_moves_the_alert_out_of_draft(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    alert_id = await _create_alert(client, zone_id)
    await _create_household(client, zone_id, "+15555591000")

    response = await client.post(f"/alerts/{alert_id}/dispatch")

    assert response.json()["status"] == "dispatching"
    assert (await client.get(f"/alerts/{alert_id}")).json()["status"] == "dispatching"


async def test_dispatch_generates_only_for_the_alerts_own_zone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    other_zone_id = await _create_zone(client, name="Hill District")
    alert_id = await _create_alert(client, zone_id)
    await _create_household(client, zone_id, "+15555592000", language="en")
    await _create_household(client, other_zone_id, "+15555592001", language="vi")

    response = await client.post(f"/alerts/{alert_id}/dispatch")

    assert response.json()["households"] == 1
    assert [prompt["target_language"] for prompt in seen] == ["en"]


async def test_dispatching_twice_does_not_give_a_household_a_second_content_row(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    alert_id = await _create_alert(client, zone_id)
    await _create_household(client, zone_id, "+15555593000")

    await client.post(f"/alerts/{alert_id}/dispatch")
    second = await client.post(f"/alerts/{alert_id}/dispatch")

    assert second.json()["content_generated"] == 0
    rows = (
        await session.exec(select(AlertContent).where(AlertContent.alert_id == uuid.UUID(alert_id)))
    ).all()
    assert len(rows) == 1


async def test_dispatch_passes_each_households_profile_to_generation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    alert_id = await _create_alert(client, zone_id)
    await _create_household(
        client,
        zone_id,
        "+15555594000",
        language="es",
        literacy_level="low_literacy",
        accessibility_needs=["asl"],
        preferred_channel="video",
    )

    await client.post(f"/alerts/{alert_id}/dispatch")

    assert seen[0]["target_language"] == "es"
    assert seen[0]["literacy_level"] == "low_literacy"
    assert seen[0]["accessibility_needs"] == ["asl"]
    assert seen[0]["channel"] == "video"
    # Facts travel as labelled structured fields, exactly as drafted.
    assert seen[0]["facts"] == {"shelter": "Lincoln High School, 400 Oak St"}


async def test_dispatch_of_a_zone_with_no_households_is_a_no_op(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _fake_claude(monkeypatch)
    zone_id = await _create_zone(client)
    alert_id = await _create_alert(client, zone_id)

    response = await client.post(f"/alerts/{alert_id}/dispatch")

    assert response.status_code == 200
    assert response.json() == {
        "alert_id": alert_id,
        "status": "dispatching",
        "households": 0,
        "content_generated": 0,
    }
    assert seen == []


async def test_dispatch_of_an_unknown_alert_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/alerts/{uuid.uuid4()}/dispatch")

    assert response.status_code == 404
