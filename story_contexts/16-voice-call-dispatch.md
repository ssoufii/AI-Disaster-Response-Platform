# Issue #16 — Dispatch an alert via voice call with TwiML playback

**Epic:** Voice channel & confirmation · **Points:** 5 · **Depends on:** #4, #12, #15
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/16

## Holistic objective

Voice is the second delivery channel after SMS, and for the households this system exists to
serve — phone-only, low-literacy, or simply more comfortable hearing a warning than reading one —
it may be the *primary* channel, not a fallback. This story proves the same generate → dispatch →
webhook → fallback pipeline already validated for SMS extends cleanly to a channel with materially
different Twilio mechanics: a live call with ringing/answer states, versus a fire-and-forget
message.

## Technical objective

- Voice branch in `backend/app/services/delivery_service.py` using Programmable Voice + TwiML
  that plays `AlertContent.voice_script` via TTS.
- Reuses the same `backend/app/webhooks/twilio_status.py` handler, status/idempotency logic
  (#8/#9), and fallback trigger (#12) — `no-answer`/`busy` are already terminal-failure statuses
  in that shared logic, so no new fallback code path is needed.
- TwiML shape depends on the #15 spike's outcome: with or without a `<Gather>` block.
- **Tests** mock Twilio Voice calls and the resulting webhook callbacks across the call's status
  progression.

Depends on #4 (voice_script content), #12 (fallback logic this channel plugs into), and #15 (the
confirmation-scope decision that shapes the TwiML built here).
