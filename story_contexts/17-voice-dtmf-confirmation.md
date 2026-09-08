# Issue #17 — Voice call captures DTMF confirmation as `confirmed_received`

**Epic:** Voice channel & confirmation · **Points:** 3 · **Depends on:** #16, #15
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/17

## Holistic objective

This story only exists to make Domain Rule 6 concrete for the voice channel: it captures the one
piece of information in the whole system that comes from an explicit human action rather than a
delivery mechanism succeeding. Conflating this with "delivered" would be a meaningful failure for
a disaster-response tool — a dispatcher believing a household confirmed safety when the phone
simply rang and played a recording is a false sense of security in exactly the moment it matters
most.

## Technical objective

- Adds a `<Gather>` to the TwiML built in #16, per the household's `voice_script` confirmation
  prompt.
- A gather-result callback (either the existing status webhook or a dedicated endpoint) sets
  `DeliveryAttempt.status` to `confirmed_received` **only** on an actual keypress — silence leaves
  the status at `delivered`, never inferred upward.
- Emits a `delivery_update` WS event on the transition so the console reflects it live.
- This entire story is void/closed without implementation if #15's decision was "delivery
  tracking only for v1" — it does not proceed independently of that spike's outcome.

Depends on #16 (needs the call in place) and #15 (only exists at all if the spike decided v1
includes two-way confirmation).
