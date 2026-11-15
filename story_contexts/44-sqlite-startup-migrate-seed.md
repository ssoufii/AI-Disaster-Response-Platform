# Issue #44 — Run on SQLite with schema and seed applied at startup

**Epic:** Live simulation demo · **Points:** 3 · **Depends on:** none
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/44

## Holistic objective

Free hosted databases expire, pause or need a card. The demo's data is invented and reset daily, so it needs no database server at all — one less thing to host and one less thing that can be down.

## Technical objective

- `aiosqlite` (and `httpx`, `greenlet`) are runtime dependencies.
- `db.py` gives SQLite a busy timeout and WAL so concurrent webhooks queue instead of failing.
- In simulation mode the app's lifespan runs `alembic upgrade head` and, on an empty database, `scripts/seed.py: seed_simulation_data` — 60 households over 3 zones, including every profile CLAUDE.md requires and one unreachable number per zone.
- Schemas label SQLite's timezone-less datetimes as the UTC they are.
