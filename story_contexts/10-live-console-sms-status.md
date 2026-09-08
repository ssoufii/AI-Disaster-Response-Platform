# Issue #10 — Dispatcher watches SMS delivery status update live on the console

**Epic:** Real-time delivery monitoring (WebSockets + console) · **Points:** 5 · **Depends on:** #7
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/10

## Holistic objective

This is the first user-facing frontend deliverable and the moment the project's "real-time
WebSocket dispatcher console" pitch becomes real. Structurally it's also risky: a naive
implementation either shows a blank screen while the socket connects, or re-renders the entire
household grid on every single update — both unacceptable for a tool meant to be watched
continuously during an actual incident.

The snapshot-then-socket pattern and the per-household reducer exist specifically to avoid both
failure modes, and both are called out explicitly in CLAUDE.md rather than left to implementation
judgment.

## Technical objective

- `backend/app/services/dispatcher_ws.py` — the WebSocket connection manager backing
  `WS /ws/alerts/{alert_id}`.
- `frontend/app/alerts/[id]/page.tsx` loads `GET /alerts/{id}/status` on mount **before** opening
  the socket, so the grid is never blank while connecting.
- `frontend/hooks/useAlertSocket.ts` owns the connection and feeds a `useReducer` keyed by
  `household_id`, so a `delivery_update` event patches exactly one row instead of re-rendering the
  grid.
- `frontend/lib/types.ts` holds the WS message TypeScript types — must stay in sync with the
  backend event shape *and* `docs/architecture.md` per CLAUDE.md's three-way-sync rule if the
  schema ever changes.
- Status colors follow the spec exactly: green=delivered/confirmed, amber=failed-but-rerouting,
  red=unreached, grey=in-flight.
- Every event must be applicable standalone — the frontend must not depend on having seen prior
  events, since a client can connect mid-stream.

Depends on #7 — there must be real delivery status changes flowing through the webhook path to
display live.
