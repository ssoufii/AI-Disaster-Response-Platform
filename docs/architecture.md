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
- Only SMS can actually be sent, so a fallback onto voice (#16) or video/WhatsApp (#19) creates its row and leaves it `queued`, logged as `delivery.channel_not_implemented`. It is deliberately **not** marked failed: nothing was tried, and calling it a failure would walk the household further down its chain for a channel this build has not built.
- When the chain runs out, the reroute logs `delivery.fallback_exhausted` and returns without a new attempt. Marking the household `unreached` and emitting the event that flags it for a human is #13's.
- A send Twilio refuses outright (an invalid number, an auth error) never gets a status callback, so it is recorded as a failed attempt and pushed to the console, but it is **not** rerouted — the trigger is the callback. Twilio's undeliverable-SMS test number (`+15005550009`, the guaranteed-fail seed household) fails that way against a live test account, so exercising that household's chain end-to-end needs a number that fails *after* Twilio accepts the message. Worth revisiting with #13, where a household that runs out of channels has to be marked `unreached` however its last attempt died.

**Implementation notes** (`backend/app/services/delivery_service.py`):
- The Twilio SDK is instantiated here and nowhere else, behind a lazily-built `get_client()` so importing the module (in tests, in Alembic) never needs credentials. The client is backed by `AsyncTwilioHttpClient` and sends via `messages.create_async`, because a zone dispatch sends from inside the request path and no blocking I/O is allowed there.
- **The `DeliveryAttempt` row is committed as `queued` before Twilio is called.** A send that raises — or a process that dies mid-call — still leaves the evidence that this household was tried. The row exists because we tried, not because Twilio answered (Domain Rule 2).
- Every send sets `status_callback` to `{PUBLIC_BASE_URL}/webhooks/twilio/status`. A send without it is a send whose outcome is unknowable, since nothing polls Twilio for status.
- A Twilio error is caught, recorded on that household's attempt as `failed` with `error_reason`, and logged; it never propagates into the dispatch loop and strands the households behind it. Rerouting a *callback-reported* failure onto the next channel is webhook-triggered (see the fallback notes above); a send Twilio refused outright stops at its failed row, because no callback is ever coming for it.
- `deliver_alert` skips households that already have an attempt for this alert, so re-dispatching never sends the same warning twice — the same promise content generation makes about its rows.
- Only the SMS channel is wired up so far. A household whose `preferred_channel` is voice (#16) or video/WhatsApp (#19) is logged and left without an attempt rather than sent something it cannot receive. That is a build-order gap, **not** Domain Rule 4's `unreached`, which means an exhausted fallback chain and is issue #13's to set.
- Delivery-path log lines carry `alert_id` and `household_id`, and phone numbers only ever appear through `redact_phone` (last 4 digits) from `app/logging_config.py`.

**Status webhook** (`backend/app/webhooks/twilio_status.py`, `POST /webhooks/twilio/status`):
- **Every callback's `X-Twilio-Signature` is validated before anything else happens.** Twilio reaches this endpoint from the public internet, so it cannot be authenticated like the rest of the API; the signature is what stands in its place. Validation lives in a FastAPI dependency (`verified_twilio_params`), so a forged callback is refused with a **403** before the handler runs and therefore before any row is read or written — the endpoint fails closed rather than process-then-reject. Without it, anyone who found the URL could post forged delivery state into the audit trail, or trigger fallback rerouting against real households.
- The signature is checked against `{PUBLIC_BASE_URL}/webhooks/twilio/status` — the same URL `delivery_service` hands Twilio as the `statusCallback`, so the two cannot drift, and the only URL that can be right in deployment: ngrok (or any proxy) terminates TLS and rewrites the host, so `request.url` is the internal address, not the one Twilio signed.
- An empty `TWILIO_AUTH_TOKEN` means nothing can be verified, so every callback is refused. A deployment that forgot the variable gets a shut endpoint, not an open one.
- A rejection is logged (`twilio_status.invalid_signature`, or `twilio_status.signature_unverifiable` when the token is missing) with the URL and whether a signature was present. Neither the auth token nor the presented signature is ever logged.
- Looks the attempt up by the SID Twilio quotes back (`MessageSid`, or the legacy `SmsSid`), maps Twilio's status onto `DeliveryStatus`, sets `completed_at` on a terminal status, records `ErrorCode: ErrorMessage` as `error_reason` on a failure, and returns. Nothing slow belongs here — no Claude generation, no outbound send — because Twilio times the callback out and retries.
- Status map: `accepted`/`queued`/`scheduled` → `queued`; `sending`/`sent` → `sending`; `delivered` → `delivered`; `undelivered`/`failed` → `failed`. `sent` is Twilio's "handed to the carrier" — still in flight, and never a receipt (Domain Rule 6). Voice's `ringing`, `in-progress`, `completed`, `no-answer` and `busy` arrive with issue #16 and are deliberately absent rather than guessed at.
- An unknown SID or an unmapped status is logged and ignored **with a 200**: a 4xx only makes Twilio retry a callback that can never be placed. The response body (`{"result": "applied" | "duplicate" | "ignored", ...}`) says which happened, so the endpoint is debuggable from the outside during an incident.
- **The handler is idempotent, because Twilio retries.** Every state Twilio reports is recorded as a `DeliveryStatusCallback` keyed on `(twilio_sid, status)`; a callback whose state is already on record is a no-op that still answers **200** (`{"result": "duplicate"}`) and is logged as `twilio_status.duplicate_callback`. Without this, a routine retry would write the same status twice and reroute the same failure twice, leaving two `DeliveryAttempt` rows for one fallback and corrupting exactly the audit trail Domain Rule 2 protects.
  - The dedupe key is `(twilio_sid, status)`, not the SID alone: `queued` → `sending` → `delivered` are different callbacks about the same message and all three must apply.
  - The `status` recorded is the **mapped** `DeliveryStatus`, not Twilio's raw string, because what must happen at most once is the *state change*. Twilio has two names for one outcome (`failed`/`undelivered`, `sending`/`sent`), and keying on the raw string would let the second name through as a fresh event — and fire a second reroute.
  - Because the key is what has been *recorded* rather than the attempt's current status, a retry of an earlier state arriving after a later one (a delayed `sending` after `delivered`) is a duplicate, so it cannot walk the row backwards.
  - The no-op returns before anything is written, which is what keeps a retried failure from also pushing a second WebSocket event to the console or creating a second fallback attempt — none of that is downstream of the guard.
  - Uniqueness is declared on the table, not only checked in the handler: two retries can arrive at once, and the lookup alone is a check-then-write both would pass. The handler's lookup keeps the ordinary retry a clean no-op; the constraint (caught as an `IntegrityError`, also answered `duplicate`) is what makes the race safe. The record is flushed in the same transaction as the status change it authorizes, so "this state was applied" and the application of it can never disagree.
- The body is read with `urllib.parse.parse_qsl` rather than `request.form()` — Twilio posts `application/x-www-form-urlencoded`, so the standard library covers it and the service needs no multipart dependency. Signature validation checks exactly that params dict, and hands it to the handler, so the body is parsed once.
- An applied update is pushed to the alert's WebSocket subscribers immediately afterwards, which is what makes the console live. The broadcast happens **after** the commit, so a console is never shown a state the database has not accepted, and it is in-process and non-blocking, so it does not slow the 200 down.
- A terminal failure (`failed`/`undelivered` now; `no-answer`/`busy` with voice, #16) schedules `delivery_service.reroute_failed_attempt` as a background task and logs `twilio_status.fallback_scheduled`. It hangs off the point where the update is applied, and therefore inherits the idempotency guard above. In-flight statuses never schedule it.

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
    twilio_status.py        # POST /webhooks/twilio/status
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

**Implementation notes** (`backend/app/services/dispatcher_ws.py`, `backend/app/schemas/ws_events.py`):
- The schema lives in `app/schemas/ws_events.py`, and is one of three things that move together: `frontend/lib/types.ts` and this document are the other two.
- Subscribers are held per `alert_id` in an in-process `ConnectionManager`. The socket is a **diff channel, never the source of truth** — nothing is replayed to a late subscriber, because the console has already read `GET /alerts/{id}/status` and the next event applies on top of it.
- **Every event is applicable standalone.** It carries the household, channel, attempt number and status in full rather than a delta, so a console that connected mid-dispatch, or reconnected after a drop (#11), lands on the same row as one that saw every earlier event.
- `fallback_triggered`/`fallback_channel` annotate the attempt that **failed**, not the one replacing it: one event saying both what went wrong and where it is going next, which is what the console renders as "SMS failed → retrying via Voice" (#14). Every other event carries them as `false`/`null`, so the console can read them unconditionally.
- A reroute therefore pushes two events, in order: the failed attempt annotated with its fallback channel, then the new attempt's own `queued` event. The second is how a console that connected between the two still learns attempt 2 exists.
- A send to a dead socket drops that subscriber and never raises. Broadcasting happens on the webhook's path, and a browser tab that closed mid-dispatch must not turn into a 500 on a Twilio callback.
- Events are emitted from two places, both after their commit: the status webhook when a callback is applied, and `delivery_service` when an attempt is first committed as `queued`, when a reroute annotates the attempt it is replacing, and if the send is refused outright. The second matters because a send Twilio never accepted gets no callback, so without it that failure would never reach the console at all.
- A single instance is assumed. A second uvicorn worker would keep its own registry and its consoles would not see these events; scaling the socket layer out is out of scope.
- `household_unreached` (#13), `dispatch_started` and `dispatch_complete` are part of the contract but are not emitted yet. The console ignores event types it does not recognise, so they can land without breaking a console that predates them.

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
    consoleState.ts          # the reducer: rows keyed by household_id
    statusTone.ts            # status → green / amber / red / grey
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
- `lib/statusTone.ts` is the single place the four colours are decided. Red is read from the household's `last_known_status`, not from any attempt, because "unreached" is a statement about the household after its whole fallback chain ran out (#13).
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

- **ASL delivery**: pre-recorded human interpreter clips per template message vs. AI avatar generation — affects scope a lot. Starting with a small library of pre-recorded common-phrase clips + Claude only writing captions is far more buildable than generating video.
- **Confirmation of safety**: do you want two-way interaction (press 1 to confirm safe) in v1, or just delivery confirmation? Two-way is a strong differentiator but adds IVR complexity.
- **Zone lookup**: simple zone_id field vs. real geofencing — geofencing is a nice-to-have, not needed for the core loop.
