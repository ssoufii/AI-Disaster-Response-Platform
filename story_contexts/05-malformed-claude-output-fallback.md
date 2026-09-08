# Issue #5 — Malformed Claude output falls back to a template

**Epic:** Claude content generation · **Points:** 3 · **Depends on:** #4
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/5

## Holistic objective

CLAUDE.md states the rule directly: "Never let a Claude failure block delivery." A disaster alert
pipeline that stalls because an LLM returned malformed JSON, or Anthropic had a transient outage,
fails the one job it exists to do — get a warning out. This story is the safety valve: it
guarantees that even total generation failure degrades gracefully to a correct, pre-written
template rather than silence.

It's named explicitly in CLAUDE.md's Testing section as required coverage, not treated as an edge
case — a reflection of how seriously the project treats "no warning" as the worst possible
outcome.

## Technical objective

- Extends `backend/app/services/content_generator.py` with a **single retry** on schema
  validation failure, then a hard fallback to
  `backend/app/services/prompts/templates.py` (pre-written per severity + language).
- Structured logging (structlog, warning level, `alert_id` + `household_id`) fires on every
  fallback so it's visible to operators watching logs during a live incident, not just buried in
  a return value.
- The resulting `AlertContent` row is **indistinguishable in shape** from a successful
  generation — downstream delivery code has no special branch for "this was a fallback."
- **Tests** mock the Anthropic client to return invalid JSON (and separately, to raise/time out)
  and assert exactly one retry occurs before the template is used, and that delivery is still
  attempted afterward.
- No regex-based free-text parsing or markdown-fence-stripping is introduced anywhere as an
  alternative strategy — the fallback is templates, not looser parsing.

Depends on #4 — this is a hardening layer directly on top of the single-household generator, not
a separate code path.
