# Issue #19 — Dispatch an ASL alert as a captioned video clip via WhatsApp/MMS

**Epic:** ASL / video channel · **Points:** 5 · **Depends on:** #18, #4, #12
**GitHub:** https://github.com/ssoufii/AI-Disaster-Response-Platform/issues/19

## Holistic objective

This closes out the channel set the project's own pitch names explicitly — "ASL video" — and
matters disproportionately for accessibility: it's the only channel serving deaf/hard-of-hearing
households, who have no equivalent fallback in SMS or voice content (a text or audio message
doesn't serve someone whose primary language is ASL). Building it last in the channel sequence is
deliberate per CLAUDE.md's build order, since by this point the generate → dispatch → webhook →
fallback skeleton has already been proven three times over — the risk here is content/asset
selection, not pipeline plumbing.

## Technical objective

- Video/ASL branch in `backend/app/services/delivery_service.py` sending via WhatsApp Business
  API or MMS with a hosted clip link, selected per the #18 decision from `asl_video_caption`.
- A default severity-level clip as a fallback when no specific match exists, so dispatch never
  fails purely on a missing asset.
- Reuses the same webhook/status/fallback machinery already built for SMS and voice — a failed
  send triggers rerouting exactly as it does for the other channels (#12).
- **Tests** mock the Twilio WhatsApp/MMS send and the resulting webhook callback.

Depends on #18 (needs the delivery mechanism decided), #4 (needs `asl_video_caption` content), and
#12 (fallback reuse).
