# Issue #14 — Console shows rerouting explained inline, not just a status flip

**Epic:** Automatic fallback rerouting · **Points:** 2 · **Depends on:** #12, #10
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/14

## Holistic objective

A status color change alone ("the SMS row went from grey to amber") tells a dispatcher something
happened, but not what, or what to do about it. CLAUDE.md's Frontend Conventions require the
reroute be stated in plain language — "SMS failed → retrying via Voice" — specifically because a
dispatcher under real time pressure during an incident shouldn't have to interpret a color code
under stress.

This is the last piece that makes the fallback-rerouting feature (#12, #13) actually usable by a
human, not just correct in the database.

## Technical objective

- Pure frontend consumer of events already emitted by #12/#13 — **no backend changes**.
- `frontend/components/HouseholdStatusGrid.tsx` renders the interpolated
  "X failed → retrying via Y" text and amber/red coloring from the `delivery_update` and
  `household_unreached` event payloads.
- `frontend/components/DeliveryTimeline.tsx` lists the full attempt history per household — each
  `DeliveryAttempt` row (per Domain Rule 2) gets its own timeline entry, rather than a single
  status flipping in place.

Depends on #12 (the fallback events it renders) and #10 (the live grid infrastructure it renders
into).
