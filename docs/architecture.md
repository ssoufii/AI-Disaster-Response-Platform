# AI Disaster Response Platform — Technical Spec

Worked backwards from the project summary:

> Built a disaster alert system that reaches households through calls, ASL video, low-literacy SMS, or native-language outreach, using the Claude API to auto-generate alert content per user and Twilio to deliver through a FastAPI backend. Engineered a real-time WebSocket dispatcher console in Next.js that lets responders monitor alert delivery status across channels and households live, re-routing failed deliveries to a fallback channel without manual intervention.

Stack: **FastAPI · Next.js · Claude API · Twilio · WebSockets**

---

## 1. Core Concept

A disaster manager (fire, flood, evacuation order, etc.) triggers an **Alert**. The system:
1. Looks up every **Household** in the affected area/zone.
2. For each household, generates **personalized alert content** with Claude based on that household's accessibility profile (language, literacy level, deaf/hard-of-hearing, phone-only, etc.).
3. Delivers it over that household's **preferred channel** via Twilio (Voice call, SMS, WhatsApp/video for ASL).
4. Tracks delivery status live over **WebSockets** in a **dispatcher console**.
5. If a channel fails (no answer, undelivered SMS, etc.), **automatically retries on a fallback channel** — no human has to notice and reroute.

---

## 2. Data Models

```
Household
- id
- name
- phone_number
- language (e.g. "es", "so", "en")
- literacy_level ("standard" | "low_literacy")
- accessibility_needs (list: "asl", "hard_of_hearing", "visually_impaired", ...)
- preferred_channel ("voice" | "sms" | "video" | "whatsapp")
- fallback_channel_order (list, e.g. ["voice", "sms"])
- zone_id
- last_known_status ("safe" | "unreached" | "unknown")

Zone
- id
- name
- geo_boundary (geojson or simple polygon/radius)

Alert
- id
- title
- raw_message (dispatcher's plain-English source alert)
- severity ("advisory" | "warning" | "evacuate_now")
- facts (JSON object: shelter, routes, times, phone numbers — the details Claude copies verbatim and may never invent; defaults to {})
- zone_id
- created_by
- created_at
- status ("draft" | "dispatching" | "completed")

AlertContent  (Claude-generated, one per household per alert)
- id
- alert_id
- household_id
- channel
- generated_text        # for SMS / voice TTS script
- generated_script       # structured script for voice call (IVR-style)
- video_caption_text     # for ASL video overlay / avatar script
- language
- generated_at

DeliveryAttempt
- id
- alert_id
- household_id
- channel
- attempt_number
- status ("queued" | "sending" | "delivered" | "failed" | "no_answer" | "confirmed_received")
- twilio_sid
- started_at
- completed_at
- error_reason (nullable)

DeliveryStatusCallback  (one row per delivery state Twilio has already reported)
- id
- delivery_attempt_id
- twilio_sid
- status                 # the mapped DeliveryStatus — half of the dedupe key
- raw_status             # Twilio's own word for it, kept for debugging only
- received_at
UNIQUE (twilio_sid, status)
```

Relationships: `Alert 1—N DeliveryAttempt`, `Household 1—N DeliveryAttempt`, `Alert+Household 1—1 AlertContent` (regenerated per alert, not reused), `DeliveryAttempt 1—N DeliveryStatusCallback`.

`DeliveryStatusCallback` exists only to make the status webhook idempotent — Twilio retries, and this table is the record of what has already been applied. It is not part of the audit trail the console reads; that is `DeliveryAttempt`. See section 4.

---

## 3. Claude API — Content Generation Layer

One backend service, `content_generator.py`, called once per household per alert. Claude's job is **transformation, not decision-making** — the dispatcher's raw alert is the source of truth; Claude adapts *form*, not *facts*.

**Design pattern: structured JSON output per household.**

Prompt inputs, sent as a structured JSON object in the user message (never concatenated into prose):
- `raw_message` (dispatcher's plain alert)
- `severity`
- `household.language`
- `household.literacy_level`
- `household.accessibility_needs`
- `household.channel`
- `alert.facts` (shelter, routes, times — labelled fields, so there is nothing for the model to drift into)

Prompt asks Claude to return JSON:
```json
{
  "sms_text": "short, <160 char, low-literacy-safe if needed, in target language",
  "voice_script": "natural spoken script for TTS/IVR, includes a Y/N confirmation prompt",
  "asl_video_caption": "short caption + plain sequential instructions for ASL interpreter/avatar",
  "language_used": "es"
}
```

Key rules to bake into the system prompt:
- Never invent facts (evacuation routes, shelter addresses) — only rephrase what's in `raw_message`; pass those as structured fields, not prose, so Claude can't drift.
- Low-literacy mode: short sentences, common words, one instruction per sentence, no jargon.
- Always include a clear action verb up front ("Evacuate now" / "Shelter in place").
- Voice script must include a confirmation prompt (e.g. "Press 1 if you are safe and evacuating") since this is what the Twilio IVR uses to mark `confirmed_received`.

This keeps Claude's role narrow, testable, and auditable — good for a disaster-response system where hallucination risk must be minimized.

**Implementation notes** (`backend/app/services/content_generator.py`):
- The response is constrained by a strict JSON schema (`output_config.format`, `additionalProperties: false`) generated from the `GeneratedAlertContent` Pydantic model in `app/schemas/alert_content.py`, and validated against that same model on the way back. `sms_text` carries a 160-character cap in the schema. Nothing parses free text — no regex, no markdown-fence stripping. A single call that fails validation raises `ContentGenerationError` from the private `_generate_once`.
- **Failure never blocks delivery.** `generate()` wraps that single call: one retry, then the pre-written template for the alert's severity and the household's language from `app/services/prompts/templates.py`. Any failure ends there — a validation failure, a timeout, a rate limit — so `generate()` does not raise, and the caller has no "this was a fallback" branch to write: a template is an ordinary `GeneratedAlertContent` and becomes an ordinary `AlertContent` row.
- Templates are hand-written per (severity, language) and carry **no alert-specific facts** — a pre-written string cannot know this alert's shelter address, and inventing one would break Domain Rule 1 more dangerously than omitting it. Each carries the action for its severity and points the household at official local alerts for specifics. An untemplated language degrades to English (with `language_used="en"`), matching how the system prompt already handles a language Claude cannot write. Templates are constructed as `GeneratedAlertContent` at import time, so one that breaks the contract fails at import rather than mid-dispatch.
- Every failed attempt and every fallback is logged at warning level with `alert_id` and `household_id`, so a Claude outage is visible in the logs during the incident rather than only in the content that went out.
- The system prompt is a frozen constant in `app/services/prompts/content_generation.py`, sent with `cache_control: ephemeral`. It is byte-identical for every household in a dispatch, which is the dispatch's single biggest cost lever.
- The generated fields map onto the `AlertContent` table as: `sms_text` → `generated_text`, `voice_script` → `generated_script`, `asl_video_caption` → `video_caption_text`, `language_used` → `language`.

**Zone fan-out** (`generate_for_zone`):
- A dispatch generates for a whole zone, so `generate_for_zone(alert, households)` runs `generate()` for every household under `asyncio.gather` with an `asyncio.Semaphore` sized by `config.CLAUDE_CONCURRENCY` (default 10). The bound is the point: hundreds of simultaneous calls rate-limit the dispatch against itself and finish slower than a paced fan-out, while too low a bound leaves a large zone waiting during an emergency. It returns one `GeneratedAlertContent` per household, in the order given, which is what lets the caller write exactly one `AlertContent` row per household.
- **A rate limit is retried, not fallen back on.** A 429 or 529 says the request was fine and the API is busy, so `generate()` gives it four attempts spaced by exponential backoff (1s, 2s, 4s) before the template — as distinct from a malformed response, which gets the single immediate retry above, since waiting does not make an invalid response valid. Without that distinction a zone that trips the rate limit would hand every remaining household a factless template while the API was merely throttling.
- Because `generate()` never raises, one household's permanent failure is one template row, never a failed batch — the other households' generations complete normally.
- No batching or rate-limit *delay* is applied at any severity, so Domain Rule 5's "`evacuate_now` skips any batching delay" needs no special case: there is none to skip.

**Dispatch wiring** (`POST /alerts/{id}/dispatch`, `app/api/alerts.py`): generation is the first phase of a dispatch — it fetches the alert's zone's households, generates for those that do not already have an `AlertContent` row for this alert, writes one row each, and moves the alert to `dispatching`. Skipping households that already have content is what makes re-dispatching safe: a household never gets a second row for the same alert. Delivery (DeliveryAttempts, Twilio sends) hangs off the same endpoint and is covered in section 4.

---

## 4. Twilio — Delivery Layer

`delivery_service.py` dispatches based on `AlertContent.channel`:

| Channel | Twilio Product | Notes |
|---|---|---|
| Voice | Programmable Voice + TwiML | Plays `voice_script` via TTS (or pre-recorded/native-speaker audio if available), gathers DTMF confirmation |
| SMS | Programmable Messaging | Sends `sms_text`; use Twilio delivery status webhooks |
| ASL / Video | WhatsApp Business API or MMS with hosted video link | Sends link to short ASL avatar/interpreter clip generated from `asl_video_caption` |
| Native language | Same Voice/SMS paths, just content is localized | Language is a content property, not a separate channel |

**Status webhooks are essential**: Twilio calls back to `/webhooks/twilio/status` on delivery/call state changes. This webhook updates `DeliveryAttempt.status` and pushes the update to the WebSocket layer immediately — this is what makes the dispatcher console "live."

**Fallback logic** (`delivery_service.reroute_failed_attempt`):
```
on DeliveryAttempt terminal failure (failed / no_answer / undelivered):
    next_channel = household.fallback_channel_order[attempt_number - 1]
    if next_channel exists:
        create new DeliveryAttempt on next_channel
        re-run content_generator if no AlertContent exists for that channel yet
        dispatch immediately
    else:
        mark household as "unreached" for this alert, flag for human follow-up
```
This loop is what the resume line "re-routing failed deliveries to a fallback channel without manual intervention" refers to. It is an async task triggered directly from the status webhook handler, not a polling cron, so it's genuinely real-time.

**Fallback implementation notes** (`reroute_failed_attempt`, `reroute`, `next_fallback_channel`):
- **The trigger is the webhook and nothing else.** `twilio_status` schedules the reroute as a FastAPI background task on a terminal failure and returns its 200 immediately; the task generates and sends on its own time. Twilio times a slow callback out and retries it, so the send can never happen inline (Domain Rule 3). Nothing polls Twilio, and nothing sweeps the table for failed attempts — `reroute_failed_attempt` has exactly one caller in the codebase.
- It is scheduled **after** the idempotency guard, so Twilio's retry of a failure — or its second name for it (`undelivered` then `failed`) — cannot schedule a second reroute and leave two fallback rows for one failure.
- The task takes an attempt *id*, not a row, and opens its own session (`db.session_scope`): by the time it runs, the request that scheduled it has answered Twilio and closed its own. It never raises — nothing is awaiting it — and logs its own failures at error level instead.
- **The failed attempt is never touched.** Its status, `error_reason` and timestamps stay exactly as the callback left them; the reroute only adds a row (Domain Rule 2). `attempt_number` increments, so the console's timeline is the chain in order.
- `fallback_channel_order` holds *only* the fallbacks — the preferred channel is attempt 1 and is not in the list — so attempt *n*'s successor is the list's *n*-th entry counting from one (`fallback_channel_order[attempt_number - 1]`). Attempt 1 on `sms` falls back to `fallback_channel_order[0]`, whose failure as attempt 2 falls back to `fallback_channel_order[1]`.
- The fallback channel gets **its own `AlertContent`**, generated before the send if this alert has none for that channel yet: an SMS read aloud down a phone line is not what the voice channel should say. `content_generator.generate` takes the channel to generate *for*, since the household's preference is by then the channel that failed, and it never raises — a Claude failure returns the template, so it can never be the reason a reroute stops.
- SMS and voice can be sent, so a fallback between them is dispatched for real. A fallback onto video/WhatsApp (#19) still creates its row and leaves it `queued`, logged as `delivery.channel_not_implemented`. It is deliberately **not** marked failed: nothing was tried, and calling it a failure would walk the household further down its chain for a channel this build has not built.
- When the chain runs out, the reroute returns without a new attempt and the household is marked **`unreached`** (see the exhaustion notes below).
- A send Twilio refuses outright (an invalid number, an auth error) never gets a status callback, so it is recorded as a failed attempt and pushed to the console, but it is **not** rerouted — the trigger is the callback. Twilio's undeliverable-SMS test number (`+15005550009`, the guaranteed-fail seed household) fails that way against a live test account, so exercising that household's chain end-to-end needs a number that fails *after* Twilio accepts the message. This is still a gap: such a household never enters the reroute path, so it is never marked `unreached` either, and the only evidence is its failed row on the console. Closing it means giving the refused-send path its own reroute trigger, which is a change to #12's "the trigger is the callback and nothing else" and belongs to a story of its own.

**Fallback exhaustion** (`delivery_service._mark_unreached`, `dispatcher_ws.broadcast_household_unreached`):
- Domain Rule 4 — "a household is never silently dropped... failing silently is the worst possible outcome here" — is enforced in exactly one place: the branch of `reroute` where `next_fallback_channel` returns `None`. There is no other path that decides a household is unreachable.
- It sets `household.last_known_status = "unreached"`, commits, logs `delivery.fallback_exhausted` at **warning** with the alert, household, last channel and attempt count, and then emits a `household_unreached` event. All three are deliberate: the log for an operator tailing an incident, the status for a dispatcher who reloads the page, the event for one already watching it. Any one of them alone leaves a way for the household to disappear.
- The status is committed **before** the broadcast, so no console is shown a household the database has not accepted as unreached.
- **The attempt rows are not touched.** Exhaustion is a verdict about the household written *beside* the chain, never over it (Domain Rule 2) — every attempt keeps its own status, `error_reason` and timestamps, and no new attempt is created.
- A household with channels still left in `fallback_channel_order` is never marked: the chain simply continues. A household whose `fallback_channel_order` is empty *is* marked on its first failure — the preferred channel was its whole chain.
- `last_known_status` lives on the household, not on the attempt, which is what the console colours red (#14) and what `GET /alerts/{id}/status` reports on a reload. It is a household-level column, so it is not scoped to one alert: a household marked `unreached` stays so until something sets it otherwise, and nothing does yet. Clearing it (on a later `delivered`, or per-alert) is not in #13's scope.
- The webhook's idempotency guard sits upstream of the reroute, so a retried failure callback cannot re-mark or re-announce a household that is already unreached.

**Implementation notes** (`backend/app/services/delivery_service.py`):
- The Twilio SDK is instantiated here and nowhere else, behind a lazily-built `get_client()` so importing the module (in tests, in Alembic) never needs credentials. The client is backed by `AsyncTwilioHttpClient` and sends via `messages.create_async` / `calls.create_async`, because a zone dispatch sends from inside the request path and no blocking I/O is allowed there.
- **The `DeliveryAttempt` row is committed as `queued` before Twilio is called.** A send that raises — or a process that dies mid-call — still leaves the evidence that this household was tried. The row exists because we tried, not because Twilio answered (Domain Rule 2).
- Every send sets `status_callback` to `{PUBLIC_BASE_URL}/webhooks/twilio/status`. A send without it is a send whose outcome is unknowable, since nothing polls Twilio for status.
- A Twilio error is caught, recorded on that household's attempt as `failed` with `error_reason`, and logged; it never propagates into the dispatch loop and strands the households behind it. Rerouting a *callback-reported* failure onto the next channel is webhook-triggered (see the fallback notes above); a send Twilio refused outright stops at its failed row, because no callback is ever coming for it.
- `deliver_alert` skips households that already have an attempt for this alert, so re-dispatching never sends the same warning twice — the same promise content generation makes about its rows.
- SMS and voice are wired up. A household whose `preferred_channel` is video/WhatsApp (#19) is logged and left without an attempt rather than sent something it cannot receive. That is a build-order gap, **not** Domain Rule 4's `unreached`, which means a fallback chain that was tried and ran out.
- The channel decides only how the send is made. Writing the row first, the `statusCallback`, the broadcast, the refused-send handling and the rerouting are shared (`_record_refused_send`, `_record_accepted_send`), so a second channel is a second `_send_*` function rather than a second delivery path.
- Delivery-path log lines carry `alert_id` and `household_id`, and phone numbers only ever appear through `redact_phone` (last 4 digits) from `app/logging_config.py`.

**Voice channel** (`delivery_service._send_voice`, `_voice_twiml`, #16):
- A household on `voice` is called with `calls.create_async`, and the TwiML is passed **inline** as the `twiml` parameter rather than fetched by Twilio from a URL of ours. The script is already written and sitting in `AlertContent.generated_script`, so an endpoint that served it back would be a second publicly reachable surface to authenticate for no gain. (The `<Gather>` below does need a callback URL, but that is an answer coming *in*, not a script going out.)
- The TwiML is a single `<Say>` of `generated_script` — the voice script, never `generated_text`. The SMS text is written to be read with the eyes; the voice script is the same facts written to be heard, which is why a fallback onto voice generates its own content. Nothing is composed around it: appending so much as a sentence here would be the delivery layer inventing words into an alert (Domain Rule 1).
- `<Say language=…>` is set from `AlertContent.language` through `VOICE_LANGUAGES` (`en` → `en-US`, `es` → `es-MX`, `vi` → `vi-VN` — the languages the generator has templates for). A warning read by a voice that cannot pronounce it is barely a warning. A language with no Twilio voice leaves the attribute off, so Twilio reads it in its default voice — wrong but audible — and logs `delivery.voice_language_unsupported` so the gap is visible rather than silent.
- The call asks for `initiated`, `ringing`, `answered` and `completed` by name (`status_callback_event`). Twilio otherwise reports only the outcome, and a console watching a dispatch would show the household at `queued` for the length of the ring.
- A call Twilio refuses outright (an unreachable number, a bad credential) takes exactly the SMS path: `failed` on the row with `error_reason`, broadcast to the console, no reroute — because no callback is coming for a call that was never placed.
- The `<Say>` is wrapped in a **`<Gather input="dtmf" numDigits="1" timeout="10">`** whose `action` is `{PUBLIC_BASE_URL}/webhooks/twilio/voice-confirmation` (#17). Every script and every fallback template ends by asking the household to press 1, so the call has to be listening for it. The script is read *inside* the gather, so a household that already knows it is safe can answer without hearing the rest of the warning out. The wait is longer than Twilio's five-second default because the household it is waiting on has just been told to evacuate.
- **Nothing follows the gather.** A household that presses nothing reaches the end of the call, which Twilio reports as `completed` and this system records as `delivered`. With `actionOnEmptyResult` left off, silence does not even reach the confirmation endpoint — Domain Rule 6's "delivery ≠ receipt" is structural here, not a check.

**Status webhook** (`backend/app/webhooks/twilio_status.py`, `POST /webhooks/twilio/status`):
- **Every callback's `X-Twilio-Signature` is validated before anything else happens.** Twilio reaches this endpoint from the public internet, so it cannot be authenticated like the rest of the API; the signature is what stands in its place. Validation lives in a FastAPI dependency (`verified_twilio_params`), so a forged callback is refused with a **403** before the handler runs and therefore before any row is read or written — the endpoint fails closed rather than process-then-reject. Without it, anyone who found the URL could post forged delivery state into the audit trail, or trigger fallback rerouting against real households.
- The signature is checked against `{PUBLIC_BASE_URL}/webhooks/twilio/status` — the same URL `delivery_service` hands Twilio as the `statusCallback`, so the two cannot drift, and the only URL that can be right in deployment: ngrok (or any proxy) terminates TLS and rewrites the host, so `request.url` is the internal address, not the one Twilio signed.
- An empty `TWILIO_AUTH_TOKEN` means nothing can be verified, so every callback is refused. A deployment that forgot the variable gets a shut endpoint, not an open one.
- A rejection is logged (`twilio_status.invalid_signature`, or `twilio_status.signature_unverifiable` when the token is missing) with the URL and whether a signature was present. Neither the auth token nor the presented signature is ever logged.
- Looks the attempt up by the SID Twilio quotes back (`MessageSid`, or the legacy `SmsSid`), maps Twilio's status onto `DeliveryStatus`, sets `completed_at` on a terminal status, records `ErrorCode: ErrorMessage` as `error_reason` on a failure, and returns. Nothing slow belongs here — no Claude generation, no outbound send — because Twilio times the callback out and retries.
- Message status map: `accepted`/`queued`/`scheduled` → `queued`; `sending`/`sent` → `sending`; `delivered` → `delivered`; `undelivered`/`failed` → `failed`. `sent` is Twilio's "handed to the carrier" — still in flight, and never a receipt (Domain Rule 6).
- Call status map (#16): `queued` → `queued`; `initiated`/`ringing`/`in-progress` → `sending`; `completed` → `delivered`; `busy`/`no-answer` → `no_answer`; `canceled`/`failed` → `failed`. A completed call is `delivered` and never `confirmed_received`: a call that was answered and heard out is still not a person saying they are safe, and only a keypress says that (#17, Domain Rule 6). `busy` and `no-answer` are both the household not taking the call, which is what `no_answer` means and why they share it — Twilio's own word for each is kept verbatim as `DeliveryStatusCallback.raw_status`.
- One endpoint serves both channels, because everything after the identification — the lookup by SID, the idempotency guard, the commit, the broadcast, the reroute — is the same work whatever was sent. What differs is only which fields Twilio uses (`MessageSid`/`MessageStatus`, or `CallSid`/`CallStatus`) and which words it puts in them, and `_identify_callback` is where that difference stops. A status from the wrong channel's vocabulary (`undelivered` on a call) is unmapped, so it is ignored with a 200 rather than guessed at.
- Because the dedupe key is the *mapped* status, a call's `initiated` → `ringing` → `in-progress` applies once as `sending` and the rest are duplicates. That is the same collapsing that makes `failed`/`undelivered` one failure; to this system they are one state — in flight — and the row is not walked through three writes of it.
- An unknown SID or an unmapped status is logged and ignored **with a 200**: a 4xx only makes Twilio retry a callback that can never be placed. The response body (`{"result": "applied" | "duplicate" | "ignored", ...}`) says which happened, so the endpoint is debuggable from the outside during an incident.
- **The handler is idempotent, because Twilio retries.** Every state Twilio reports is recorded as a `DeliveryStatusCallback` keyed on `(twilio_sid, status)`; a callback whose state is already on record is a no-op that still answers **200** (`{"result": "duplicate"}`) and is logged as `twilio_status.duplicate_callback`. Without this, a routine retry would write the same status twice and reroute the same failure twice, leaving two `DeliveryAttempt` rows for one fallback and corrupting exactly the audit trail Domain Rule 2 protects.
  - The dedupe key is `(twilio_sid, status)`, not the SID alone: `queued` → `sending` → `delivered` are different callbacks about the same message and all three must apply.
  - The `status` recorded is the **mapped** `DeliveryStatus`, not Twilio's raw string, because what must happen at most once is the *state change*. Twilio has two names for one outcome (`failed`/`undelivered`, `sending`/`sent`), and keying on the raw string would let the second name through as a fresh event — and fire a second reroute.
  - Because the key is what has been *recorded* rather than the attempt's current status, a retry of an earlier state arriving after a later one (a delayed `sending` after `delivered`) is a duplicate, so it cannot walk the row backwards.
  - The no-op returns before anything is written, which is what keeps a retried failure from also pushing a second WebSocket event to the console or creating a second fallback attempt — none of that is downstream of the guard.
  - Uniqueness is declared on the table, not only checked in the handler: two retries can arrive at once, and the lookup alone is a check-then-write both would pass. The handler's lookup keeps the ordinary retry a clean no-op; the constraint (caught as an `IntegrityError`, also answered `duplicate`) is what makes the race safe. The record is flushed in the same transaction as the status change it authorizes, so "this state was applied" and the application of it can never disagree.
- The body is read with `urllib.parse.parse_qsl` rather than `request.form()` — Twilio posts `application/x-www-form-urlencoded`, so the standard library covers it and the service needs no multipart dependency. Signature validation checks exactly that params dict, and hands it to the handler, so the body is parsed once.
- An applied update is pushed to the alert's WebSocket subscribers immediately afterwards, which is what makes the console live. The broadcast happens **after** the commit, so a console is never shown a state the database has not accepted, and it is in-process and non-blocking, so it does not slow the 200 down.
- A terminal failure (a message's `failed`/`undelivered`, a call's `no-answer`/`busy`/`canceled`/`failed`) schedules `delivery_service.reroute_failed_attempt` as a background task and logs `twilio_status.fallback_scheduled`. It hangs off the point where the update is applied, and therefore inherits the idempotency guard above. In-flight statuses never schedule it.
- **A confirmed attempt is never written over** (#17). If the attempt is already `confirmed_received`, any other status is logged as `twilio_status.after_confirmation` and answered `{"result": "ignored", "reason": "attempt already confirmed_received"}` — nothing is recorded, nothing is broadcast, nothing is rerouted. This is the ordinary sequence rather than an edge case: the keypress arrives mid-call and Twilio reports `completed` only once the call is over, so without the guard every confirmation would be overwritten by `delivered` seconds later. A terminal failure reported after a confirmation is dropped for the same reason — a household that has said it is safe is not called again on the next channel because the call it said it on then dropped.

**Confirmation webhook** (`backend/app/webhooks/twilio_status.py`, `POST /webhooks/twilio/voice-confirmation`, #17):
- Twilio posts here from the voice call's `<Gather>`, quoting the call's `CallSid` and the `Digits` pressed. A `1` is a household saying it is safe, and it is the **only** input in the system that writes `confirmed_received` (Domain Rule 6). Nothing infers it from call duration, answer status or a voicemail pickup.
- Its signature is validated exactly as the status webhook's is, against **its own** URL (`verified_gather_params` → `{PUBLIC_BASE_URL}/webhooks/twilio/voice-confirmation`), because the signature covers the URL Twilio posted to. A callback signed for the status webhook is refused here with a 403, and so is an unsigned one: this is the endpoint that can mark a household safe, so a forged keypress is precisely what the check exists to stop.
- Any other digit, an empty gather, a missing `CallSid` or a SID that is not ours leaves the attempt exactly where the call's own status put it, logged as `twilio_confirmation.not_a_confirmation` / `.malformed_callback` / `.unknown_sid`. A household reaching for the keypad and missing has not told anyone anything.
- A confirmation is recorded through the same `(twilio_sid, status)` guard the status webhook uses, so Twilio's retry — or a household pressing 1 twice — applies once and pushes one WebSocket event.
- On a confirmation the attempt's `status` becomes `confirmed_received` and `completed_at` is set: a household that has answered is waiting on nothing further. It is a status update on **the existing attempt**, never a new one (Domain Rule 2), and it is not a failure, so it reroutes nothing.
- The commit comes first and the `delivery_update` broadcast second, as everywhere else — no console is shown a household as safe before the database has accepted it. That event is how a dispatcher watching the grid sees the household drop off the list of people who still need someone to go and knock.
- The response is always **TwiML and always 200**, because Twilio plays back whatever a gather's `action` URL returns and retries anything else. It is an empty `<Response/>`, which ends a call whose warning has been read and answered; saying anything further would be the delivery layer composing words into a call whose content is Claude's (Domain Rule 1) — and in one language, into calls placed in three.

---

## 5. FastAPI Backend

```
/app
  main.py
  api/
    alerts.py         # POST /alerts, POST /alerts/{id}/dispatch
    households.py      # CRUD
    zones.py
    dispatch_status.py # GET /alerts/{id}/status (snapshot for page load)
  services/
    content_generator.py   # Claude calls
    delivery_service.py     # Twilio calls + fallback logic
    dispatcher_ws.py        # WebSocket connection manager
  webhooks/
    twilio_status.py        # POST /webhooks/twilio/status, POST /webhooks/twilio/voice-confirmation
  models/
    household.py, alert.py, delivery.py  (SQLAlchemy or SQLModel)
  db.py
```

Key endpoints:
- `POST /alerts` — dispatcher drafts an alert (raw_message, severity, zone_id)
- `POST /alerts/{id}/dispatch` — triggers: fetch households in zone → generate content per household (async, e.g. `asyncio.gather` with concurrency limit) → create DeliveryAttempts → send via Twilio. Returns `{alert_id, status, households, content_generated, deliveries_started}`; `deliveries_started` can legitimately trail `households`, since a household on a channel still ahead in the build order is counted but not attempted. Delivery *outcomes* never come back from here — the send only hands the message to Twilio, and whether it arrived is reported later by webhook.
- `GET /alerts/{id}/status` — full current snapshot (used on dispatcher console page load, before WebSocket takes over). Lives in `api/alerts.py` alongside the other alert routes. Returns `{alert_id, status, households: [...]}`, one entry per household **in the alert's zone** — including households with no attempt yet, whose `current_attempt` is `null`, because a household missing from the snapshot is a household nobody is watching:
  ```json
  {
    "household_id": "...", "name": "Baker household",
    "preferred_channel": "sms", "last_known_status": "unknown",
    "current_attempt": {
      "id": "...", "channel": "sms", "attempt_number": 1, "status": "delivered",
      "twilio_sid": "SM...", "started_at": "...", "completed_at": "...", "error_reason": null
    }
  }
  ```
  `current_attempt` is the household's highest `attempt_number` — attempts are ordered by the fallback chain that produced them, not by wall clock. Earlier attempts stay exactly where they are; this only picks which one the snapshot shows. The full per-household attempt history is the DeliveryTimeline's (#14).
- `WS /ws/alerts/{id}` — dispatcher console subscribes here for live updates
- `POST /webhooks/twilio/status` — Twilio calls this on every delivery state change; a request whose `X-Twilio-Signature` does not validate is refused with a 403 before any state is touched
- `POST /webhooks/twilio/voice-confirmation` — the voice call's `<Gather>` action URL: Twilio posts the digit the household pressed, `1` marks that attempt `confirmed_received`, and the response is TwiML. Signature-validated against its own URL, and refused with a 403 the same way

**WebSocket message schema** (server → client):
```json
{
  "type": "delivery_update",
  "alert_id": "...",
  "household_id": "...",
  "channel": "sms",
  "status": "failed",
  "attempt_number": 1,
  "fallback_triggered": true,
  "fallback_channel": "voice",
  "timestamp": "..."
}
```
```json
{
  "type": "household_unreached",
  "alert_id": "...",
  "household_id": "...",
  "last_channel": "voice",
  "attempts_made": 2,
  "last_known_status": "unreached",
  "timestamp": "..."
}
```

**Implementation notes** (`backend/app/services/dispatcher_ws.py`, `backend/app/schemas/ws_events.py`):
- The schema lives in `app/schemas/ws_events.py`, and is one of three things that move together: `frontend/lib/types.ts` and this document are the other two.
- Subscribers are held per `alert_id` in an in-process `ConnectionManager`. The socket is a **diff channel, never the source of truth** — nothing is replayed to a late subscriber, because the console has already read `GET /alerts/{id}/status` and the next event applies on top of it.
- **Every event is applicable standalone.** It carries the household, channel, attempt number and status in full rather than a delta, so a console that connected mid-dispatch, or reconnected after a drop (#11), lands on the same row as one that saw every earlier event.
- `fallback_triggered`/`fallback_channel` annotate the attempt that **failed**, not the one replacing it: one event saying both what went wrong and where it is going next, which is what the console renders as "SMS failed → retrying via Voice" (#14). Every other event carries them as `false`/`null`, so the console can read them unconditionally.
- A reroute therefore pushes two events, in order: the failed attempt annotated with its fallback channel, then the new attempt's own `queued` event. The second is how a console that connected between the two still learns attempt 2 exists.
- A send to a dead socket drops that subscriber and never raises. Broadcasting happens on the webhook's path, and a browser tab that closed mid-dispatch must not turn into a 500 on a Twilio callback.
- Events are emitted from three places, all after their commit: the status webhook when a callback is applied, the confirmation webhook when a household presses 1 (#17), and `delivery_service` when an attempt is first committed as `queued`, when a reroute annotates the attempt it is replacing, and if the send is refused outright. The last matters because a send Twilio never accepted gets no callback, so without it that failure would never reach the console at all.
- A `confirmed_received` `delivery_update` is an ordinary event on the existing attempt — same `channel` and `attempt_number`, no fallback fields set — so the console patches one row green off it with no new contract. It is the only status on this socket that came from a person rather than from Twilio.
- A single instance is assumed. A second uvicorn worker would keep its own registry and its consoles would not see these events; scaling the socket layer out is out of scope.
- `household_unreached` is the second event on this socket and the only one about a *household* rather than an attempt: its whole fallback chain has been tried and nothing landed, so the next move belongs to a person (Domain Rule 4). It carries `last_known_status: "unreached"` rather than a delivery status — the grid colours a row red on what the household *is*, which is also what the snapshot reports for it on a reload — plus `last_channel` and `attempts_made`, so a console that connected after the fact can still say what was tried.
- It is emitted **alongside** the failed attempt's own `delivery_update`, not instead of it: "this channel did not land" and "there is no next channel" are two facts, and the console needs both.
- The console forwards it to its reducer and renders the red "needs a human" row from it (#14). `dispatch_started` and `dispatch_complete` are part of the contract and not emitted at all yet; the console ignores event types it does not recognise, so both can land without breaking a console that predates them.

---

## 6. Next.js Dispatcher Console

```
/app
  alerts/[id]/page.tsx     # main live console
  components/
    AlertConsole.tsx         # client half: owns the reducer and the socket
    ConnectionBanner.tsx     # "connection lost / reconnecting" while the socket is down
    HouseholdStatusGrid.tsx  # live grid: household x channel x status
    AlertComposer.tsx        # form to draft + dispatch a new alert
    DeliveryTimeline.tsx     # per-household attempt history incl. fallback events
  hooks/
    useAlertSocket.ts        # React wrapper: frame parsing + connection state
  lib/
    alertSocketController.ts # connect / backoff / reconnect / resync, outside React
    types.ts                 # snapshot + WS event types, mirrored from the backend schemas
    consoleState.ts          # the reducer: rows keyed by household_id, each with its attempt history
    statusTone.ts            # status → green / amber / red / grey
    rerouteNarrative.ts      # the same state in words: "SMS failed → retrying via Voice"
    api.ts, env.ts           # GET /alerts/{id}/status; NEXT_PUBLIC_* URLs
  tests/                     # node --test (`npm test`); no test framework installed
```

- On mount: `GET /alerts/{id}/status` for initial snapshot, then open WebSocket for live diffs — avoids a blank screen while the socket connects.
- State: reducer keyed by `household_id` so incoming `delivery_update` events patch just one row instead of re-rendering the whole grid.
- Visually distinguish: delivered (green), failed→rerouted (amber, shows "SMS failed → retrying via Voice"), unreached after all fallbacks (red, needs human follow-up).
- Reconnect with backoff if the socket drops — a dispatcher console silently going stale during a disaster is the worst failure mode.

**Implementation notes:**
- `app/alerts/[id]/page.tsx` is a **server component**, and that is what makes the snapshot-before-socket rule structural rather than a matter of ordering effects: the grid is rendered on the server and shipped as HTML, and the socket opens underneath a console that is already showing every household. Its failure paths render a panel saying the console cannot reach the dispatch service — never a blank page, because a dispatcher has to be able to tell "nothing is happening" from "this console is broken".
- `AlertConsole.tsx` is the `"use client"` boundary. It seeds `useReducer` from the snapshot it was handed, so its first client paint is the same grid the server rendered.
- `lib/consoleState.ts` holds rows in a `Record<household_id, HouseholdRow>` plus a separate `order` array. A `delivery_update` replaces one entry and leaves every other row object identical, so the memoised rows around it do not re-render — on a zone dispatch that is the difference between one row updating and hundreds re-rendering per Twilio callback. `order` is kept apart so a patch never reshuffles the grid.
- An event for a household the snapshot did not contain is dropped: it joined the zone after the page loaded, and a reconnect resync is what picks it up.
- `lib/statusTone.ts` is the single place the four colours are decided. Red is read from the household's `last_known_status`, not from any attempt, because "unreached" is a statement about the household after its whole fallback chain ran out (#13). Amber is any attempt annotated with a fallback, whatever the failing status was called, so a terminal status added to the chain later does not leave a household being retried looking grey.
- `lib/rerouteNarrative.ts` turns the same row into the sentence beside it — "SMS failed → retrying via Voice", or "All channels tried after 2 attempts — needs human follow-up" (#14). Built from the event payload's `fallback_triggered`/`fallback_channel` rather than from what the console remembers, so a console that connected mid-dispatch prints the same words as one that watched the whole thing. A colour alone is something a dispatcher has to decode under time pressure; that is why the words are required by CLAUDE.md's Frontend Conventions and not left to the palette.
- `HouseholdRow.attempts` accumulates one entry per `attempt_number`, which `components/DeliveryTimeline.tsx` lists oldest-first in a `<details>` disclosure per household. Attempts accumulate rather than flip in place because a fallback creates a *new* DeliveryAttempt and never mutates the failed one (Domain Rule 2) — a single status changing colour would be a different claim about what happened. A resync keeps the attempts the console already saw and lets the snapshot decide only where the household stands now: an observed attempt is a committed row nothing rewrites, while `GET /alerts/{id}/status` reports one current attempt per household and cannot restore a history.
- `useAlertSocket.ts` holds its handlers in a ref so a new inline callback per render does not tear the socket down, ignores frames it cannot parse or whose `type` it does not know, and returns the connection state the banner renders from. The lifecycle underneath it lives in `lib/alertSocketController.ts`.
- `lib/alertSocketController.ts` owns connect → drop → backoff → reconnect → resync, deliberately outside React and outside the browser: the socket, the snapshot fetch and the timer all arrive as arguments, which is what makes the backoff schedule and the resync observable in `frontend/tests/` without a DOM or a live server. Three rules it exists to enforce:
  - **A drop is reported before the retry is waited out**, not after. At the tail of the schedule "after" is half a minute of a console that looks fine and is not.
  - **Backoff is 1s, 2s, 4s, 8s, 16s, then 30s from there on, forever.** Capped rather than given up on — a long outage is exactly when the console must not quietly stop trying, so the banner persists through every failed attempt instead of clearing on an *attempt*.
  - **A reopened socket is not `live` until it has resynced.** Nothing replays the events missed during an outage, so the controller re-fetches `GET /alerts/{id}/status`, reseeds the reducer from it (`snapshot_resync`), and only then clears the banner. Frames that arrive while that fetch is in flight are held and applied *after* the snapshot, or the older snapshot would land on top of newer state. A resync that fails drops the connection back into the retry loop rather than clearing the banner.
- That resync is the console's **first browser-side read of the API** — the load snapshot is fetched by a server component and a WebSocket handshake is not subject to CORS, so nothing before it was. The console is its own origin by design (that is what `NEXT_PUBLIC_API_URL` is for), so `main.py` adds `CORSMiddleware` over `CONSOLE_ORIGINS` (comma-separated, default `http://localhost:3000`), GET only, named origins, no wildcard and no credentials. Without it the resync is blocked and the banner can never clear, which is the exact "console stuck looking broken" state the story exists to prevent. Auth on these routes is #20's.
- The frontend suite runs on the Node test runner (`npm test` in `frontend/`) — no test framework is a dependency, and the injected socket/fetch/timer are why none is needed.
- Reads `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` as literal `process.env.*` member accesses, the form Next inlines at build time.

---

## 7. Suggested Build Order

1. **Data layer**: models + FastAPI CRUD for Household/Zone/Alert, seed script with sample households (varied language/literacy/accessibility).
2. **Claude content generation**: get `content_generator.py` producing solid JSON for a single household, hand-test edge cases (low-literacy Spanish, ASL caption).
3. **Twilio sandbox delivery**: wire up SMS first (simplest), confirm status webhook round-trip updates DB.
4. **WebSocket plumbing**: connection manager + `/ws/alerts/{id}`, push a fake update, confirm the frontend receives it.
5. **Next.js console v1**: static grid rendering from `GET /status`, then wire the socket in.
6. **Fallback logic**: implement and test with an intentionally-failing Twilio number.
7. **Voice + video/ASL channels**: extend once SMS path is solid end-to-end.
8. **Polish**: retry backoff, rate limiting on Claude calls, auth on dispatcher console.

## 8. Environment / Config Needed

```
ANTHROPIC_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_WHATSAPP_NUMBER=
DATABASE_URL=
PUBLIC_BASE_URL=   # for Twilio webhook callbacks
CONSOLE_ORIGINS=   # comma-separated console origins allowed to read the API from the browser
```

---

## Open Design Decisions to Make Early

- **ASL delivery**: pre-recorded human interpreter clips per template message vs. AI avatar generation — affects scope a lot. Starting with a small library of pre-recorded common-phrase clips + Claude only writing captions is far more buildable than generating video. *(Still open — #18.)*
- **Confirmation of safety**: do you want two-way interaction (press 1 to confirm safe) in v1, or just delivery confirmation? Two-way is a strong differentiator but adds IVR complexity. *(**Decided** — see "Decision: IVR confirmation" below.)*
- **Zone lookup**: simple zone_id field vs. real geofencing — geofencing is a nice-to-have, not needed for the core loop.

---

## Decision: IVR confirmation

**v1 includes two-way DTMF confirmation.** Voice calls use `<Gather>`, a keypress of `1` is the
only thing in the system that writes `DeliveryStatus.CONFIRMED_RECEIVED`, and **#17 proceeds as
scoped** — it is not closed as out of scope. Decided under #15 (time-boxed spike, no production
code); this note is the whole deliverable.

**Why, given the complexity/value tradeoff:**

- **The content side is already built and already promises it.** Every voice script the system
  produces ends by asking the household to press 1: the system prompt requires it
  (`prompts/content_generation.py`), and all three severity fallback templates hard-code it
  (`prompts/templates.py`). Shipping voice without a gather means placing calls that instruct
  people to press a key nothing is listening for. That is worse than not asking — a household
  that pressed 1 believes it has told somebody it is safe, while the console still shows
  `delivered` and a responder is still planning to go and knock on the door. Delivery-tracking-only
  would therefore not be a smaller v1; it would be a v1 that has to go back and strip the
  confirmation prompt out of four generated-content paths.
- **Domain Rule 6 is inert without it.** `CONFIRMED_RECEIVED` is already in `DeliveryStatus`, and
  the rule says it may arrive only from explicit human action. With delivery tracking only, no code
  path can ever reach that value and the rule degrades to "never set this". "Delivery ≠ receipt" is
  the distinction this system exists to hold; it is worth stating only if the second state is
  reachable.
- **The marginal complexity is one TwiML verb and one route.** Voice is being built regardless
  (#16), so TwiML generation and the call-status path are sunk cost. The delta is a
  `<Gather numDigits="1" action=...>` around the `<Say>`, plus a gather-result endpoint — and that
  endpoint reuses machinery that is already built and proven on SMS: signature validation (#8),
  `(twilio_sid, status)` idempotency (#9), commit-then-broadcast to the console (#10). No new model
  column, therefore **no migration**; no new dependency; no new external service. The IVR
  complexity the original note worried about is the interactive *menu* case — branching, retries,
  speech input — and none of that is in scope: one prompt, one digit, no menu.
- **It is the console's point.** At zone scale `delivered` says almost nothing about who is safe.
  A dispatcher triages by finding the households that still need a person, and
  `confirmed_received` is the only signal that removes a household from that list on the household's
  own say-so.

**What this decision does not cover:**

- **Voice only.** SMS-reply confirmation — the other human action Domain Rule 6 allows — is not in
  v1 and has no story. Adding it later is a new story, not a widening of #17.
- **Silence is never a receipt.** A call that completes with no keypress stays `delivered`, always.
  Nothing infers confirmation from call duration, answer status or a voicemail pickup.
- **A confirmation is a status update on the existing attempt, never a new one** (Domain Rule 2),
  and it is not a terminal failure, so it triggers no fallback.
- **The split between #16 and #17 stands.** #16 ships call placement, TwiML playback and voice
  status handling; #17 adds the `<Gather>` and its callback. Between the two merging there is a
  window where calls say "press 1" and nothing listens — accepted only because #17 follows
  immediately in the same epic, which is precisely why it is not optional.

**Reversing it** costs nothing but this note while #17 is unstarted: no schema, no config and no
other story depends on the choice. After #17 merges, reversal also means removing the gather
callback and the confirmation sentence from the prompt and the templates.
