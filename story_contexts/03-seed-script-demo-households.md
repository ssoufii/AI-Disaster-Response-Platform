# Issue #3 — Seed script populates demo households across accessibility/channel profiles

**Epic:** Foundation & data layer · **Points:** 3 · **Depends on:** #1
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/3

## Holistic objective

CLAUDE.md is explicit that no channel or fallback story is "done" until it's been exercised
against fixture households spanning every accessibility/language/channel combination — plus one
household whose phone number is guaranteed to fail, because the fallback-rerouting feature (the
system's headline capability) can only be proven with a real failure to reroute from.

Without this seed script, every later story (#7, #12, #16, #19) would require hand-crafting test
data ad hoc, which risks inconsistent coverage and makes the failing-number fixture easy to
forget under deadline pressure. This story turns that requirement into a single repeatable
command that the whole rest of the backlog can rely on.

## Technical objective

- `backend/scripts/seed.py`, invoked via `uv run python scripts/seed.py`.
- Reuses the Household/Zone models and schemas from #1 — no new models introduced here.
- **Idempotency**: upserts on a stable key (e.g. `phone_number`) so rerunning the script never
  duplicates households — required because seed data will get re-run often during development.
- **Required coverage**: at least one household each for low-literacy English, Spanish voice,
  ASL/video, a non-English SMS household, and one guaranteed-to-fail number.
- The guaranteed-fail household should use a Twilio magic test number (a documented
  "always undelivered" test SID) rather than a made-up invalid number, so it behaves correctly
  against both the mocked test client and a real Twilio sandbox.
