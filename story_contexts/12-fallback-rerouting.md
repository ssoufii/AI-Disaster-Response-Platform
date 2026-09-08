# Issue #12 — SMS failure automatically reroutes to the household's fallback channel

**Epic:** Automatic fallback rerouting · **Points:** 5 · **Depends on:** #7, #9
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/12

## Holistic objective

This is the feature the whole project is named for in its own pitch — "re-routing failed
deliveries to a fallback channel without manual intervention." Everything before this issue
(content generation, SMS delivery, live console) exists to make this moment possible and visible.

It's also where two of the six Domain Rules converge at once: Rule 2 (every attempt is a new row,
never a mutation) and Rule 3 (fallback is webhook-driven, not polled). Get either wrong and the
audit trail becomes unreliable, or the system becomes something other than "genuinely real-time" —
a polling loop checking every N seconds is not the same product as one that reacts the instant
Twilio reports a failure.

## Technical objective

- Triggered as an **async task launched directly from** `backend/app/webhooks/twilio_status.py`
  on a terminal failure status (`failed`, `undelivered`, `no-answer`, `busy`) — never on in-flight
  statuses (`queued`, `sending`, `ringing`).
- `backend/app/services/delivery_service.py` selects
  `household.fallback_channel_order[attempt_number]`, generates content for that channel if none
  exists yet (reuses #4/#5), creates a **new** `DeliveryAttempt` with `attempt_number` incremented
  (the failed row is left untouched), and dispatches immediately.
- Emits a `delivery_update` WS event with `fallback_triggered=true` and `fallback_channel` set.
- No polling loop or cron job anywhere in the codebase — the trigger point is the webhook handler
  itself.
- Exercised end-to-end against the guaranteed-fail seed household from #3.

Depends on #7 (needs the base delivery path to fail against) and #9 (fallback must not double-fire
on a duplicate webhook delivery).
