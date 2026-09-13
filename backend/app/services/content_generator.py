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

A rate limit is the one failure worth waiting out rather than falling back on.
A 429 or 529 means "ask again shortly", not "this response is broken", so it
earns extra attempts spaced by exponential backoff — otherwise a zone-wide
dispatch that trips the rate limit would hand every remaining household a
template while the API was merely busy.

``generate_for_zone`` is the whole-zone entry point: the same per-household
contract run concurrently, bounded by ``config.CLAUDE_CONCURRENCY``.
"""

import asyncio
import json
from collections.abc import Sequence
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

# A rate limit is a different kind of failure: the request was fine, the API is
# busy. Four attempts spaced 1s, 2s, 4s ride out roughly seven seconds of
# throttling, which is what a large zone's fan-out tends to provoke, without
# leaving a household waiting long enough to matter.
MAX_RATE_LIMIT_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 1.0

# 429 is Anthropic's rate limit; 529 is "overloaded". Both mean "retry shortly".
RATE_LIMIT_STATUS_CODES = frozenset({429, 529})


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


def _is_rate_limited(exc: Exception) -> bool:
    """Whether an exception is the API asking us to slow down.

    Duck-typed on ``status_code`` rather than matched against the SDK's
    exception classes: every Anthropic error that carries an HTTP status exposes
    it, and nothing else in this path does.
    """
    return getattr(exc, "status_code", None) in RATE_LIMIT_STATUS_CODES


async def generate(alert: Alert, household: Household) -> GeneratedAlertContent:
    """Generate alert content for one household, or fall back to a template.

    Calls Claude, retrying once if the call fails or the response does not
    validate, and retrying a rate limit (429/529) further with exponential
    backoff. When the attempts are spent, returns the pre-written template for
    the alert's severity and the household's language and logs the fallback at
    warning level so it is visible to whoever is watching logs during the
    incident. Never raises: an undeliverable household is a worse outcome than
    a generic warning.
    """
    log = logger.bind(alert_id=str(alert.id), household_id=str(household.id))
    attempt = 0
    backoff = INITIAL_BACKOFF_SECONDS

    while True:
        attempt += 1
        try:
            return await _generate_once(alert, household)
        except Exception as exc:
            # Deliberately broad: a validation failure, a timeout, and a
            # transport error all mean the same thing to this household — no
            # content yet — and all of them end at the same template. Only a
            # rate limit is treated differently, because only a rate limit says
            # the request itself was fine.
            rate_limited = _is_rate_limited(exc)
            attempts_allowed = MAX_RATE_LIMIT_ATTEMPTS if rate_limited else MAX_ATTEMPTS
            log.warning(
                "content_generation.attempt_failed",
                attempt=attempt,
                attempts_allowed=attempts_allowed,
                rate_limited=rate_limited,
                model=settings.CLAUDE_MODEL,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if attempt >= attempts_allowed:
                break
            if rate_limited:
                await asyncio.sleep(backoff)
                backoff *= 2

    content = template_for(alert.severity, household.language)
    log.warning(
        "content_generation.template_fallback",
        severity=alert.severity,
        requested_language=household.language,
        language_used=content.language_used,
        attempts=attempt,
    )
    return content


async def generate_for_zone(
    alert: Alert, households: Sequence[Household]
) -> list[GeneratedAlertContent]:
    """Generate content for every household in a zone, concurrently but bounded.

    Returns one ``GeneratedAlertContent`` per household, in the order the
    households were given.

    A zone is hundreds of households, so the fan-out runs concurrently — but
    behind a semaphore sized by ``config.CLAUDE_CONCURRENCY``, because a
    dispatch that opens hundreds of simultaneous calls rate-limits itself and
    ends up slower than one that paces itself.

    One household never sinks the batch: ``generate`` does not raise, so a
    household whose generation permanently fails takes its template and every
    other household completes normally.

    No batching delay is applied at any severity, so Domain Rule 5's
    "``evacuate_now`` skips any batching delay" needs no special case here —
    there is none to skip.
    """
    log = logger.bind(alert_id=str(alert.id))
    semaphore = asyncio.Semaphore(settings.CLAUDE_CONCURRENCY)

    async def generate_bounded(household: Household) -> GeneratedAlertContent:
        async with semaphore:
            return await generate(alert, household)

    log.info(
        "content_generation.zone_started",
        households=len(households),
        concurrency=settings.CLAUDE_CONCURRENCY,
    )
    contents = await asyncio.gather(*(generate_bounded(h) for h in households))
    log.info("content_generation.zone_complete", households=len(contents))
    return list(contents)


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
