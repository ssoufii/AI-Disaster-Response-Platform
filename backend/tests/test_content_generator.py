"""Per-household content generation.

The Anthropic client is replaced wholesale in every test — ``get_client`` is
monkeypatched, so no test can reach the network even with an API key in the
environment (CLAUDE.md, Testing).

The fact-preservation tests are the point of this file: they are the concrete,
checkable form of "Claude never invents facts" (Domain Rule 1). A fact that
exists only in the alert's ``facts`` object must travel into the prompt as a
labelled field and come back out verbatim.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.exceptions import ContentGenerationError
from app.models.alert import Alert
from app.models.enums import Channel, LiteracyLevel, Severity
from app.models.household import Household
from app.services import content_generator
from app.services.prompts import CONTENT_SYSTEM_PROMPT

SHELTER_ADDRESS = "Lincoln High School, 400 Oak St"

VALID_RESPONSE = {
    "sms_text": f"Evacuate now. Go to {SHELTER_ADDRESS}. Leave before 6pm.",
    "voice_script": (
        f"Evacuate now. Go to {SHELTER_ADDRESS}. Leave before 6pm. "
        "Press 1 if you are safe and leaving."
    ),
    "asl_video_caption": f"Evacuate now.\nGo to {SHELTER_ADDRESS}.\nLeave before 6pm.",
    "language_used": "es",
}


def _alert(**overrides: object) -> Alert:
    defaults: dict[str, object] = {
        "title": "Flash flood evacuation",
        "raw_message": "Evacuate the riverside area immediately.",
        "severity": Severity.EVACUATE_NOW.value,
        "facts": {"shelter": SHELTER_ADDRESS, "routes": ["Route 9 north"], "leave_by": "18:00"},
        "zone_id": uuid.uuid4(),
    }
    return Alert(**(defaults | overrides))


def _household(**overrides: object) -> Household:
    defaults: dict[str, object] = {
        "name": "Familia Reyes",
        "phone_number": "+15555550101",
        "language": "es",
        "literacy_level": LiteracyLevel.LOW_LITERACY.value,
        "accessibility_needs": [],
        "preferred_channel": Channel.SMS.value,
        "fallback_channel_order": [Channel.VOICE.value],
        "zone_id": uuid.uuid4(),
    }
    return Household(**(defaults | overrides))


def _fake_client(payload: object, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install a stand-in Anthropic client returning ``payload`` as its text block.

    ``payload`` is serialized if it is a dict, or sent as-is if it is already a
    string — which is how the malformed-response tests deliver junk.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(
        content_generator,
        "get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    return create


async def test_generate_returns_validated_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_client(VALID_RESPONSE, monkeypatch)

    content = await content_generator.generate(_alert(), _household())

    assert content.sms_text == VALID_RESPONSE["sms_text"]
    assert content.voice_script == VALID_RESPONSE["voice_script"]
    assert content.asl_video_caption == VALID_RESPONSE["asl_video_caption"]
    assert content.language_used == "es"


async def test_facts_go_in_as_structured_fields_and_come_back_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert = _alert()
    create = _fake_client(VALID_RESPONSE, monkeypatch)

    content = await content_generator.generate(alert, _household())

    # In: the shelter address is a labelled field, not prose Claude has to find.
    sent = json.loads(create.await_args.kwargs["messages"][0]["content"])
    assert sent["facts"]["shelter"] == SHELTER_ADDRESS
    assert SHELTER_ADDRESS not in alert.raw_message  # it exists *only* in facts

    # Out: the same address, character for character, on every channel.
    assert SHELTER_ADDRESS in content.sms_text
    assert SHELTER_ADDRESS in content.voice_script
    assert SHELTER_ADDRESS in content.asl_video_caption


async def test_prompt_inputs_are_structured_not_concatenated_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = _fake_client(VALID_RESPONSE, monkeypatch)

    await content_generator.generate(_alert(), _household())

    sent = json.loads(create.await_args.kwargs["messages"][0]["content"])
    assert sent == {
        "severity": Severity.EVACUATE_NOW.value,
        "raw_message": "Evacuate the riverside area immediately.",
        "target_language": "es",
        "literacy_level": LiteracyLevel.LOW_LITERACY.value,
        "accessibility_needs": [],
        "channel": Channel.SMS.value,
        "facts": {"shelter": SHELTER_ADDRESS, "routes": ["Route 9 north"], "leave_by": "18:00"},
    }


async def test_system_prompt_is_cached_and_identical_across_households(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert = _alert()
    create = _fake_client(VALID_RESPONSE, monkeypatch)

    await content_generator.generate(alert, _household())
    await content_generator.generate(alert, _household(phone_number="+15555550102"))

    first, second = (call.kwargs["system"] for call in create.await_args_list)
    assert first[0]["cache_control"] == {"type": "ephemeral"}
    assert first[0]["text"] == CONTENT_SYSTEM_PROMPT
    # Byte-identical between households, which is what makes the cache hit.
    assert first == second


async def test_model_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    create = _fake_client(VALID_RESPONSE, monkeypatch)

    await content_generator.generate(_alert(), _household())

    assert create.await_args.kwargs["model"] == settings.CLAUDE_MODEL


async def test_response_is_constrained_by_a_strict_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = _fake_client(VALID_RESPONSE, monkeypatch)

    await content_generator.generate(_alert(), _household())

    output_format = create.await_args.kwargs["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "sms_text",
        "voice_script",
        "asl_video_caption",
        "language_used",
    }
    assert schema["properties"]["sms_text"]["maxLength"] == 160


async def test_low_literacy_sms_over_160_characters_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_client({**VALID_RESPONSE, "sms_text": "Evacuate now. " * 20}, monkeypatch)

    with pytest.raises(ContentGenerationError):
        await content_generator.generate(_alert(), _household())


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        f"```json\n{json.dumps(VALID_RESPONSE)}\n```",  # fences are not stripped
        json.dumps({"sms_text": "Evacuate now."}),  # missing required fields
        json.dumps({**VALID_RESPONSE, "shelter_hotline": "555-0100"}),  # extra field
    ],
)
async def test_unvalidatable_response_fails_loudly(
    payload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_client(payload, monkeypatch)

    with pytest.raises(ContentGenerationError):
        await content_generator.generate(_alert(), _household())


async def test_response_without_a_text_block_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        content_generator,
        "get_client",
        lambda: SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(content=[])))
        ),
    )

    with pytest.raises(ContentGenerationError):
        await content_generator.generate(_alert(), _household())
