# Issue #9 — Twilio status webhook is idempotent under duplicate callbacks

**Epic:** SMS delivery path (end-to-end) · **Points:** 3 · **Depends on:** #7
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/9

## Holistic objective

Twilio retries webhook deliveries by design — this is documented Twilio behavior, not a bug to
work around. Without idempotency, a routine retry could double-apply a status change, and worse,
could fire the fallback-rerouting logic (#12) twice for the same failure, creating duplicate
`DeliveryAttempt` rows that corrupt the audit trail Domain Rule 2 exists to protect.

This is required test coverage per CLAUDE.md's Testing section, precisely because it's the kind
of bug that only surfaces under real Twilio retry behavior in production — a demo dispatch that
never hits a retry window will never expose it.

## Technical objective

- Dedupe key on `(twilio_sid, status)` inside `backend/app/webhooks/twilio_status.py`: before
  applying an update, check whether this exact `(sid, status)` pair has already been recorded, and
  no-op if so — while still returning **200** (Twilio must not see a failure, or it will keep
  retrying and the problem compounds).
- Different status values for the same `sid` (e.g. `sending` → `delivered`) are legitimate
  progressions and must both apply — the dedupe key is `(sid, status)`, not just `sid`.
- A no-op duplicate must not emit a duplicate WebSocket event and must not create a duplicate
  fallback `DeliveryAttempt`.
- If a dedup lookup requires a new column/index on `DeliveryAttempt`, include the Alembic revision
  in the same commit.

Depends on #7 — same handler, additional invariant layered on top of it (independent of the
signature check in #8).
