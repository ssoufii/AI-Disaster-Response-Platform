# Story contexts

One file per backlog issue (see `../docs/backlog.md`), each explaining the story's **holistic
objective** (why it matters to the project, what it unlocks) and its **technical objective**
(files/modules touched, contracts it establishes or depends on).

Read the relevant file here before starting work on a story — it's meant to be the "why and how"
briefing that precedes execution, not a restatement of the issue's acceptance criteria.

| # | File | Title |
|---|---|---|
| 1 | [01-dispatcher-registers-households.md](01-dispatcher-registers-households.md) | Dispatcher registers households within a zone |
| 2 | [02-dispatcher-drafts-alert.md](02-dispatcher-drafts-alert.md) | Dispatcher drafts an alert for a zone |
| 3 | [03-seed-script-demo-households.md](03-seed-script-demo-households.md) | Seed script populates demo households across accessibility/channel profiles |
| 4 | [04-generate-content-single-household.md](04-generate-content-single-household.md) | Generate personalized alert content for one household |
| 5 | [05-malformed-claude-output-fallback.md](05-malformed-claude-output-fallback.md) | Malformed Claude output falls back to a template |
| 6 | [06-generate-content-zone-concurrency.md](06-generate-content-zone-concurrency.md) | Generate content for an entire zone with bounded concurrency |
| 7 | [07-dispatch-sms-webhook-tracking.md](07-dispatch-sms-webhook-tracking.md) | Dispatch an alert via SMS and track delivery status via webhook |
| 8 | [08-reject-invalid-webhook-signatures.md](08-reject-invalid-webhook-signatures.md) | Reject Twilio webhooks with invalid signatures |
| 9 | [09-webhook-idempotency.md](09-webhook-idempotency.md) | Twilio status webhook is idempotent under duplicate callbacks |
| 10 | [10-live-console-sms-status.md](10-live-console-sms-status.md) | Dispatcher watches SMS delivery status update live on the console |
| 11 | [11-console-reconnect-banner.md](11-console-reconnect-banner.md) | Console shows a reconnecting banner and recovers after a dropped WebSocket |
| 12 | [12-fallback-rerouting.md](12-fallback-rerouting.md) | SMS failure automatically reroutes to the household's fallback channel |
| 13 | [13-household-unreached-exhaustion.md](13-household-unreached-exhaustion.md) | Household marked unreached after exhausting all fallback channels |
| 14 | [14-console-rerouting-legible.md](14-console-rerouting-legible.md) | Console shows rerouting explained inline, not just a status flip |
| 15 | [15-spike-ivr-confirmation-decision.md](15-spike-ivr-confirmation-decision.md) | SPIKE: Decide two-way IVR confirmation approach for v1 |
| 16 | [16-voice-call-dispatch.md](16-voice-call-dispatch.md) | Dispatch an alert via voice call with TwiML playback |
| 17 | [17-voice-dtmf-confirmation.md](17-voice-dtmf-confirmation.md) | Voice call captures DTMF confirmation as `confirmed_received` |
| 18 | [18-spike-asl-delivery-decision.md](18-spike-asl-delivery-decision.md) | SPIKE: Decide ASL delivery mechanism for v1 |
| 19 | [19-asl-video-dispatch.md](19-asl-video-dispatch.md) | Dispatch an ASL alert as a captioned video clip via WhatsApp/MMS |
| 20 | [20-hardening-auth-ratelimit-redaction.md](20-hardening-auth-ratelimit-redaction.md) | Harden the platform: console auth, Twilio/Claude rate limiting & backoff, phone-number log redaction audit |
