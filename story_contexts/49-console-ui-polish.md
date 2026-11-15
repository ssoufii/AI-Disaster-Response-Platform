# Issue #49 — Polish the console and landing page UI

**Epic:** Live simulation demo · **Points:** 5 · **Depends on:** #47
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/49

## Holistic objective

The console is the project's face. It should answer its one question at a glance — has this alert reached everyone, and who needs a person — and look like a tool someone would trust in an incident.

## Technical objective

- The chrome has no colour; the only hues are the four status tones, so colour always means a household's status. Tokens (light and dark) live in `app/globals.css`.
- A sentence naming who needs follow-up, four totals that add up to the zone, and a reach strip with one cell per household (`components/ReachSummary.tsx`, `lib/tally.ts`).
- The grid's "Channel path" column shows every attempt ("~~SMS~~ → Voice"), fed by a new `attempts` list on the status snapshot. A retry in flight stays amber with "SMS failed → retrying via Voice".
- Motion is limited to colour cross-fades and a short entry on the reroute sentence, and respects reduced motion.
