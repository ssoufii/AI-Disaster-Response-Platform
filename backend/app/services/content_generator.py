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

A failure is not, however, allowed to stop an alert. ``generate`` retries once
and then returns the pre-written template for the household's severity and
language, so a Claude outage or a malformed response degrades to a generic
correct warning rather than silence (CLAUDE.md: "Never let a Claude failure
block delivery"). The template is an ordinary ``GeneratedAlertContent`` —
callers have no "this one was a fallback" branch to write.
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
from app.services.prompts import CONTENT_SYSTEM_PROMPT, template_for

logger = structlog.get_logger(__name__)

# Four short fields in any language; generous enough that a long voice script
# is never truncated, small enough that a runaway generation fails fast.
MAX_TOKENS = 2048

# The first call plus a single retry. One retry covers the transient case — a
# truncated response, a blip — without spending a live incident's minutes
# re-asking a model that is clearly not answering. After that, the template.
MAX_ATTEMPTS = 2


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
    """Generate alert content for one household, or fall back to a template.

    Calls Claude, retrying once if the call fails or the response does not
    validate. If the retry fails too, returns the pre-written template for the
    alert's severity and the household's language and logs the fallback at
    warning level so it is visible to whoever is watching logs during the
    incident. Never raises: an undeliverable household is a worse outcome than
    a generic warning.
    """
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await _generate_once(alert, household)
        except Exception as exc:
            # Deliberately broad: a validation failure, a timeout, a 429, and a
            # transport error all mean the same thing to this household — no
            # content yet — and all of them end at the same template.
            log.warning(
                "content_generation.attempt_failed",
                attempt=attempt,
                attempts_allowed=MAX_ATTEMPTS,
                model=settings.CLAUDE_MODEL,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    content = template_for(alert.severity, household.language)
    log.warning(
        "content_generation.template_fallback",
        severity=alert.severity,
        requested_language=household.language,
        language_used=content.language_used,
        attempts=MAX_ATTEMPTS,
    )
    return content


async def _generate_once(alert: Alert, household: Household) -> GeneratedAlertContent:
    """One Claude call, validated strictly.

    Raises ``ContentGenerationError`` if the response does not validate against
    ``GeneratedAlertContent``. Anything the SDK raises propagates untouched.
    ``generate`` is what turns either of those into a logged retry.
    """
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
        raise ContentGenerationError("Claude returned no text block")

    try:
        return GeneratedAlertContent.model_validate_json(raw)
    except ValidationError as exc:
        # Deliberately no salvage attempt — no regex, no fence stripping.
        raise ContentGenerationError(
            f"Claude response failed schema validation ({exc.error_count()} errors)"
        ) from exc
