# Issue #13 — Household marked unreached after exhausting all fallback channels

**Epic:** Automatic fallback rerouting · **Points:** 3 · **Depends on:** #12
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/13

## Holistic objective

Rule 4 in CLAUDE.md is the starkest line in the whole document: "A household is never silently
dropped... failing silently is the worst possible outcome here." Rerouting (#12) handles the case
where a fallback exists; this story handles the case where it doesn't — the chain runs out.

In a real disaster, this is the difference between a household that got no warning through any
channel being flagged for a human to physically check on, versus that household simply vanishing
from view with no signal anyone ever sees. This is arguably the single most important failure path
in the entire backlog.

## Technical objective

- Appended to the same fallback logic from #12 in `backend/app/services/delivery_service.py`:
  when `fallback_channel_order` has no next entry, set `household.last_known_status = "unreached"`.
- Emits a `household_unreached` WS event — a new event type alongside `delivery_update`,
  `dispatch_started`, and `dispatch_complete`.
- `GET /alerts/{id}/status` must reflect this status on a fresh page load too, not just live via
  WS — a dispatcher who reloads mid-incident must still see who needs follow-up, matching the
  "console shows a snapshot before the socket" rule from #10.
- A household with channels still remaining in `fallback_channel_order` must **not** be marked
  unreached — only true exhaustion triggers this.

Depends on #12 — this is the terminal branch of that same fallback logic, not a separate mechanism.
