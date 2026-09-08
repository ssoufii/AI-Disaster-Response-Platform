# Issue #11 — Console shows a reconnecting banner and recovers after a dropped WebSocket

**Epic:** Real-time delivery monitoring (WebSockets + console) · **Points:** 3 · **Depends on:** #10
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/11

## Holistic objective

CLAUDE.md states this as plainly as any requirement in the document: a dispatcher console that
silently goes stale during a disaster is a critical failure — arguably worse than no console at
all, because a dispatcher trusts a stale "all green" screen and stops looking elsewhere for
problems. This story exists purely to make failure *visible* rather than hiding it — the
frontend's counterpart to Domain Rule 4 (never fail silently) on the backend side.

## Technical objective

- Exponential backoff reconnect logic inside `frontend/hooks/useAlertSocket.ts`, with connection
  state exposed to the page component (not buried inside the hook).
- A visible banner component appears immediately on disconnect and persists through failed
  reconnect attempts — it must not disappear just because a reconnect *attempt* was made if that
  attempt didn't succeed.
- On a successful reconnect, the console re-fetches `GET /alerts/{id}/status` to resync state
  (events missed during the outage are otherwise lost) before trusting WS diffs again, then the
  banner clears.
- **Tests** mock the WebSocket to simulate a drop, verify backoff timing/attempts, and verify the
  resync-then-clear sequence on reconnect.

Depends on #10 — there has to be a live socket connection in place before this story can
meaningfully test losing and recovering it.
