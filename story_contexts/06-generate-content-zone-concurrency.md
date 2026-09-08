# Issue #6 — Generate content for an entire zone with bounded concurrency

**Epic:** Claude content generation · **Points:** 5 · **Depends on:** #4, #5
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/6

## Holistic objective

A real dispatch isn't one household — it's a zone, potentially hundreds of them, all needing
personalized content within seconds of an "evacuate now" being issued. This story is what makes
the single-household generator (#4) and its fallback safety net (#5) usable at the scale the
project's own pitch implies.

Getting the concurrency bound wrong in either direction is a real operational risk: too high and
Anthropic starts rate-limiting the whole batch (429s cascading across households), too low and a
large zone dispatch takes too long to be useful in an actual emergency. This story is where that
tradeoff gets made deliberately, as a config value, rather than left to chance.

## Technical objective

- `asyncio.gather` with a semaphore sized by `config.CLAUDE_CONCURRENCY` (default 10) — bounds how
  many `generate()` calls are in flight at once.
- Exponential backoff retry on `429`/`529` at the **batch** level — distinct from the
  single-retry-then-template logic in #5, which handles per-call malformed output rather than
  rate limits.
- A single permanently-failing household must **not** fail the whole batch — it falls back per #5
  while every other household's generation completes normally.
- Wired into `backend/app/api/alerts.py`'s `POST /alerts/{id}/dispatch` as the content-generation
  phase that runs before delivery begins.
- **Tests** mock the Anthropic client to simulate concurrency limits, a 429-then-success sequence,
  and one permanent failure amid many successes.

Depends on #4 and #5 — it's fan-out over the same `generate()` contract, plus the malformed-output
safety net, applied concurrently rather than sequentially.
