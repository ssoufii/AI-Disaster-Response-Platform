# Issue #1 — Dispatcher registers households within a zone

**Epic:** Foundation & data layer · **Points:** 8 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/1

## Holistic objective

Every downstream capability in this system — Claude generating personalized content, Twilio
delivering it, the console showing live status, fallback rerouting — operates *per household*.
None of that can exist until there's a concrete record of who needs to be reached and how. This
issue is the foundation stone: it stands up the two entities (`Zone`, `Household`) that every
other piece of the pipeline reads from.

Concretely, it answers: "who lives in the area under threat, and what does each of them need to
receive an alert they can actually understand and act on?" That's not just a phone number — it's
language, literacy level, accessibility needs (deaf/hard-of-hearing, visually impaired, etc.), a
preferred channel, and an ordered fallback chain if that channel fails. Without this data captured
up front, Claude has nothing to personalize against and Twilio has nothing to route on — the
entire "reaches households through calls, ASL video, low-literacy SMS, or native-language
outreach" premise of the project depends on this profile existing and being queryable.

It's scoped as a *vertical slice*, not "build the models": the story is done when a dispatcher can
actually create a zone, register households into it via the API, and read them back — not just
when SQLModel classes compile.

## Technical objective

- **Models** (`backend/app/models/zone.py`, `household.py`, SQLModel): `Zone` holds `id`, `name`,
  `geo_boundary`; `Household` holds `id`, `name`, `phone_number`, `language`, `literacy_level`,
  `accessibility_needs` (list), `preferred_channel`, `fallback_channel_order` (list), `zone_id`
  (FK), `last_known_status`.
- **Schemas** (`backend/app/schemas/`): separate Pydantic request/response models per CLAUDE.md's
  rule that ORM objects never get returned directly from routes.
- **DB plumbing** (`backend/app/db.py`): async SQLAlchemy engine + session dependency — this is
  the first story to touch it, so it's built here rather than in a standalone "infra" ticket.
- **Endpoints** (`backend/app/api/zones.py`, `households.py`): `POST /zones`, `POST /households`,
  `GET /zones/{id}/households`, wired into `backend/app/main.py`'s router registration.
- **Migrations**: first Alembic revision, generated via `alembic init` +
  `alembic revision --autogenerate`, committed alongside the models per CLAUDE.md's migration
  rule.
- **Validation boundary**: a `zone_id` that doesn't exist on `POST /households` must 404/422 and
  create nothing — the one explicit failure-path AC, catching referential integrity early since
  every later story assumes a household's zone is always valid.

Everything after this issue — content generation (#4), SMS dispatch (#7), fallback logic (#12) —
reads `Household.language`, `accessibility_needs`, `preferred_channel`, and
`fallback_channel_order` directly, so the shape decided here is effectively the schema contract
for the rest of the backlog.
