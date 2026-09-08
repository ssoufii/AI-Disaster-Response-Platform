# Issue #8 — Reject Twilio webhooks with invalid signatures

**Epic:** SMS delivery path (end-to-end) · **Points:** 2 · **Depends on:** #7
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/8

## Holistic objective

The status webhook built in #7 is, by necessity, a public endpoint — Twilio has to reach it
without authentication. That also makes it the single most attackable surface in the backend:
anyone who finds the URL could POST fake "delivered" or "failed" statuses and manipulate delivery
records, or worse, trigger bogus fallback rerouting (#12) against real households.

CLAUDE.md calls signature validation "non-negotiable" for exactly this reason — it's the only
thing standing between a public URL and forged delivery state in a system whose entire value
proposition is an accurate, trustworthy audit trail.

## Technical objective

- Twilio's `RequestValidator`, using `config.TWILIO_AUTH_TOKEN` and `config.PUBLIC_BASE_URL` to
  reconstruct and check the `X-Twilio-Signature` header inside
  `backend/app/webhooks/twilio_status.py`.
- An invalid or missing signature short-circuits to **403** before any DB write happens — the
  handler must fail closed, not process-then-reject.
- The rejection is logged for observability, without ever logging the auth token itself.
- **Tests** cover both the valid-signature pass-through case and the invalid/missing-signature
  403 case — this is one of the four required test cases named explicitly in CLAUDE.md's Testing
  section.

Depends on #7 — this adds a guard in front of the handler #7 built, it doesn't change its
business logic.
