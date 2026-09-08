# Issue #15 — SPIKE: Decide two-way IVR confirmation approach for v1

**Epic:** Voice channel & confirmation · **Points:** 2 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/15

## Holistic objective

CLAUDE.md flags this as one of exactly two genuinely open decisions in the whole project —
whether v1 tracks voice delivery only, or adds two-way "press 1 if safe" confirmation. That
distinction is not cosmetic: Domain Rule 6 draws a hard line between `delivered` (Twilio handed it
off) and `confirmed_received` (a human explicitly responded), and that second state literally
cannot exist without an IVR gather mechanism.

Deciding this before touching voice code prevents building #16/#17 against an assumption that
turns out wrong, which would mean reworking TwiML and `DeliveryAttempt` status handling mid-build
instead of scoping it correctly up front.

## Technical objective

- No code. A time-boxed (1 day) research spike.
- Deliverable: a written **"Decision: IVR confirmation"** note appended to `docs/architecture.md`,
  stating the choice, the rationale (complexity vs. value tradeoff), and whether `<Gather>` is
  used at all.
- The decision must explicitly state what happens to #17 (DTMF confirmation story): proceed as
  scoped, or close it as out of scope for v1.
- Nothing merges as production code under this issue — only the decision document.
