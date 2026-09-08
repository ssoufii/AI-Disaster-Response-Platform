# Issue #7 — Dispatch an alert via SMS and track delivery status via webhook

**Epic:** SMS delivery path (end-to-end) · **Points:** 5 · **Depends on:** #4, #2
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/7

## Holistic objective

This is the first fully closed loop in the system — draft, generate, send, and get confirmation
back — and CLAUDE.md deliberately sequences it before any WebSocket/console work so the
send→webhook→DB path is proven with a boring `GET` request first, rather than debugging Twilio
integration and real-time UI simultaneously.

It's also where Domain Rule 2 ("every delivery attempt is a row") first becomes real: this story
creates the `DeliveryAttempt` table and its very first row. Every later story — fallback,
exhaustion, live console — is built on the assumption that this table's shape and update path are
already correct.

## Technical objective

- `backend/app/services/delivery_service.py` — the *only* place the Twilio SDK is instantiated.
- Every send sets `statusCallback` to `{PUBLIC_BASE_URL}/webhooks/twilio/status`.
- `backend/app/webhooks/twilio_status.py` receives and applies status updates.
- `backend/app/models/delivery_attempt.py`: `id`, `alert_id`, `household_id`, `channel`,
  `attempt_number`, `status`, `twilio_sid`, `started_at`, `completed_at`, `error_reason` +
  Alembic revision.
- `GET /alerts/{id}/status` added to `backend/app/api/alerts.py` as the snapshot endpoint the
  console will later poll on load (#10).
- **Tests** mock the Twilio client entirely — no live send.

Depends on #4 (needs `AlertContent.sms_text` to send) and #2 (needs a real Alert to dispatch).
