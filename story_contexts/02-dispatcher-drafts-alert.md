# Issue #2 — Dispatcher drafts an alert for a zone

**Epic:** Foundation & data layer · **Points:** 3 · **Depends on:** #1
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/2

## Holistic objective

Before anything can be personalized or delivered, there needs to be a captured statement of what
the disaster manager is actually declaring — the raw message, its severity, and which zone it
targets. This is the object that flows through the entire pipeline: content generation reads
`raw_message` + `severity`, delivery attempts reference `alert_id`, and the WebSocket contract
keys every event off it. Domain Rule 5 (severity gates behavior, not just wording) can't be
enforced until severity is a real field on a real `Alert` row.

This story is intentionally narrow — it only covers drafting, not dispatching — so the Alert
lifecycle (`draft` → `dispatching` → `completed`) has a clean starting state to build the rest of
the pipeline against, instead of conflating "an alert exists" with "an alert is being sent."

## Technical objective

- **Model** (`backend/app/models/alert.py`): `id`, `title`, `raw_message`, `severity`, `zone_id`,
  `created_by`, `created_at`, `status`.
- **Endpoints** (`backend/app/api/alerts.py`): `POST /alerts`, `GET /alerts/{id}`.
- **Schema** (`backend/app/schemas/alert.py`): restricts `severity` to
  `advisory | warning | evacuate_now` — anything else is a validation error, not a silently
  accepted string.
- **Migration**: second Alembic revision (after Household/Zone from #1) for the `Alert` table.
- **Referential check**: a `zone_id` that doesn't exist on `POST /alerts` returns a not-found
  error and creates nothing — reuses the Zone model from #1.

No external calls (Claude, Twilio) are involved in this story — it's pure CRUD, which is why it's
sized smaller than #1 despite superficially similar shape.
