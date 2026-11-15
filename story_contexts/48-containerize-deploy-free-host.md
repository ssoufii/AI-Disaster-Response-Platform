# Issue #48 — Containerize and deploy the backend to a free host (Koyeb)

**Epic:** Live simulation demo · **Points:** 3 · **Depends on:** #44, #45
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/48

## Holistic objective

The demo is only a demo if it is reachable. It has to run somewhere that costs nothing and needs no secrets.

## Technical objective

- `backend/Dockerfile`: Python 3.12, `uv sync --frozen --no-dev`, one uvicorn worker (the simulator, scheduler and socket registry live in process memory), SQLite in the container's disk.
- Health check is the existing `GET /health`.
- README has the Koyeb steps; the host account and the `DEMO_API_URL` variable are the owner's to create.
