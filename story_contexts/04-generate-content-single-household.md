# Issue #4 — Generate personalized alert content for one household

**Epic:** Claude content generation · **Points:** 5 · **Depends on:** #1, #2
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/4

## Holistic objective

This is where the project's central premise — Claude adapts *form*, never invents *facts* — gets
implemented and locked down as a contract. It's the single most safety-critical piece of the
system: a disaster alert system that lets an LLM drift on shelter addresses or evacuation routes
is actively dangerous, not just buggy.

This story proves, for one household, that structured facts go in and structured, validated,
fact-preserving content comes out — before any fan-out (#6) or failure-handling (#5) complexity
is layered on top. Getting this single-household contract right first means every later story
inherits a generator that's already been proven not to hallucinate.

## Technical objective

- `backend/app/services/content_generator.py` — the *only* place the Anthropic SDK is
  instantiated, per CLAUDE.md's strict service-boundary rule.
- **Structured output only**: response validated against a Pydantic `AlertContent` schema — no
  regex parsing, no markdown-fence stripping, ever.
- System prompt lives in `backend/app/services/prompts/` as a versioned constant, sent with
  **prompt caching** (`cache_control`) since it's identical across every household in a dispatch —
  CLAUDE.md calls this out as the single biggest cost lever.
- `backend/app/models/alert_content.py` + Alembic revision for the `AlertContent` table.
- `config.CLAUDE_MODEL` sourced from `app/config.py` — no bare model-ID strings inline.
- Expected shape: `sms_text`, `voice_script`, `asl_video_caption`, `language_used`.
- **Tests** mock the Anthropic client entirely and assert that a fact present *only* in the input
  `facts` object appears verbatim in the output — the concrete, checkable form of "never invents
  facts."

Depends on #1 (Household profile fields) and #2 (Alert.raw_message/severity/facts) since
generation reads both.
