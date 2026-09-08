# Issue #20 — Harden the platform: console auth, Twilio/Claude rate limiting & backoff, phone-number log redaction audit

**Epic:** Hardening (auth, rate limiting, observability) · **Points:** 8 · **Depends on:** #10, #16, #19
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/20

## Holistic objective

Every prior story assumed a trusted, single-operator environment — no login wall, no rate
ceiling, no audit against PII leakage. That's a reasonable posture for building and proving the
core loop, but not for something that would ever touch real households' real data during a real
event.

This story is CLAUDE.md's explicit "polish" stage, and its coarse-grained scoping in this backlog
is a deliberate tradeoff to stay near the issue-count cap — it's flagged for decomposition into
three separate stories (auth, rate limiting, redaction) before anyone actually picks it up, since
each has genuinely independent risk and testing surface.

## Technical objective

- **Auth**: guard on backend routes (`main.py` dependency) and the WS handshake, plus a matching
  frontend guard around the console — unauthenticated requests to `/alerts/*` or the WS endpoint
  are rejected before any data or events are served.
- **Rate limiting / backoff**: exponential backoff at Twilio/Claude call sites in
  `delivery_service.py` and `content_generator.py`, hardened beyond the per-batch behavior already
  built in #6, to survive a large real dispatch without tripping provider rate limits.
- **Redaction**: a structlog processor enforcing CLAUDE.md's "last 4 digits only" phone-number
  rule across every delivery-path log line — audited across all channels built by this point
  (#7, #16, #19).
- Depends on #10 (console to gate), #16 and #19 (all delivery paths that need rate-limit polish
  and redaction coverage) since it audits/hardens work already merged, rather than building new
  features.

**Before starting:** split into (a) console auth, (b) Twilio/Claude retry & rate-limit polish,
(c) structured logging + redaction audit — each independently sized and testable.
