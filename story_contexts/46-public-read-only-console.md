# Issue #46 — Public read-only console access in simulation mode

**Epic:** Live simulation demo · **Points:** 5 · **Depends on:** #45
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/46

## Holistic objective

#20 closed the console behind the dispatcher token, correctly. The demo needs the opposite for exactly two reads and a socket, and nothing more — a visitor must not be able to dispatch, register or change anything.

## Technical objective

- `auth.py` lets a GET through without a token only when `SIMULATION_MODE` is on and the route template is in `PUBLIC_DEMO_READS` (`/alerts/latest`, `/alerts/{alert_id}/status`). The alert socket is accepted without a token in the same mode.
- New `GET /alerts/latest` returns the newest dispatched alert with `zone_name` and `next_dispatch_at`.
- Everything else still returns 401 anonymously; tests assert each route.
