"""Claude content generation.

The only place in the codebase where the Anthropic SDK is instantiated
(CLAUDE.md, Backend Conventions). Callers hand in an Alert and a Household and
get back validated content; they never see the SDK.

Two rules shape everything here:

1. Claude rewrites form, never facts. The dispatcher's ``raw_message`` and the
   structured ``facts`` object are the source of truth, and they are passed as
   structured JSON rather than concatenated prose so there is nothing for the
   model to drift into.
2. Output is validated against a strict JSON schema. A response that does not
   validate is a failure, never something to repair by parsing free text.

Retry-and-fall-back-to-a-template is deliberately *not* here: this module fails
loudly, and issue #5 layers the retry/template contract on top.
"""

import json
from functools import lru_cache
from typing import Any

import structlog
from anthropic import AsyncAnthropic
from pydantic import ValidationError

from app.config import settings
from app.exceptions import ContentGenerationError
from app.models.alert import Alert
from app.models.household import Household
from app.schemas.alert_content import GeneratedAlertContent
from app.services.prompts import CONTENT_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Four short fields in any language; generous enough that a long voice script
# is never truncated, small enough that a runaway generation fails fast.
MAX_TOKENS = 2048


@lru_cache
def get_client() -> AsyncAnthropic:
    """The process-wide Anthropic client.

    Built lazily so importing this module (in tests, in Alembic) never requires
    an API key, and cached so a zone-wide fan-out shares one connection pool.
    """
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


def _prompt_inputs(alert: Alert, household: Household) -> dict[str, Any]:
    """The per-household half of the prompt, as structured fields.

    Facts travel as a JSON object, not prose: a shelter address Claude is asked
    to *copy* from a labelled field is far harder to drift on than one buried in
    a paragraph.
    """
    return {
        "severity": alert.severity,
        "raw_message": alert.raw_message,
        "target_language": household.language,
        "literacy_level": household.literacy_level,
        "accessibility_needs": household.accessibility_needs,
        "channel": household.preferred_channel,
        "facts": alert.facts,
    }


async def generate(alert: Alert, household: Household) -> GeneratedAlertContent:
    """Generate alert content for one household.

    Raises ``ContentGenerationError`` if the response does not validate against
    ``GeneratedAlertContent``.
    """
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))

    response = await get_client().messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        # The system prompt is identical for every household in a dispatch, so
        # it is marked cacheable — one write, then a cache read per household.
        system=[
            {
                "type": "text",
                "text": CONTENT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    _prompt_inputs(alert, household), sort_keys=True, ensure_ascii=False
                ),
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": GeneratedAlertContent.model_json_schema(),
            }
        },
    )

    raw = next((block.text for block in response.content if block.type == "text"), None)
    if raw is None:
        log.warning("content_generation.no_text_block", model=settings.CLAUDE_MODEL)
        raise ContentGenerationError("Claude returned no text block")

    try:
        return GeneratedAlertContent.model_validate_json(raw)
    except ValidationError as exc:
        # Deliberately no salvage attempt — no regex, no fence stripping.
        log.warning(
            "content_generation.invalid_response",
            model=settings.CLAUDE_MODEL,
            error_count=exc.error_count(),
        )
        raise ContentGenerationError("Claude response failed schema validation") from exc
