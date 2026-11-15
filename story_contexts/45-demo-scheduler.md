# Issue #45 — Demo scheduler dispatches a mock alert every N minutes and resets nightly

**Epic:** Live simulation demo · **Points:** 3 · **Depends on:** #42, #43, #44
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/45

## Holistic objective

A public demo has nobody to press dispatch. Without a clock a visitor lands on a finished alert and sees nothing happen.

## Technical objective

- `services/demo_scheduler.py` runs only in simulation mode, started from `main.py`'s lifespan.
- Each tick dispatches the next alert from a fixed hand-written pool through `services/dispatch.py` — the same function the route calls — unless the previous dispatch is still reporting statuses.
- Once a day it wipes and re-seeds the database.
- It is not fallback polling: rerouting stays webhook-driven (Domain Rule 3).
