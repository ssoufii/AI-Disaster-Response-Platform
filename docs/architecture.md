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
```

Relationships: `Alert 1—N DeliveryAttempt`, `Household 1—N DeliveryAttempt`, `Alert+Household 1—1 AlertContent` (regenerated per alert, not reused).

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

**Fallback logic** (in `delivery_service.py` or a background worker):
```
on DeliveryAttempt terminal failure (failed / no_answer / undelivered):
    next_channel = household.fallback_channel_order[attempt_number]
    if next_channel exists:
        create new DeliveryAttempt on next_channel
        re-run content_generator if no AlertContent exists for that channel yet
        dispatch immediately
    else:
        mark household as "unreached" for this alert, flag for human follow-up
```
This loop is what the resume line "re-routing failed deliveries to a fallback channel without manual intervention" refers to — implement it as an async task triggered directly from the status webhook handler, not a polling cron, so it's genuinely real-time.

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
- `POST /alerts/{id}/dispatch` — triggers: fetch households in zone → generate content per household (async, e.g. `asyncio.gather` with concurrency limit) → create DeliveryAttempts → send via Twilio
- `GET /alerts/{id}/status` — full current snapshot (used on dispatcher console page load, before WebSocket takes over)
- `WS /ws/alerts/{id}` — dispatcher console subscribes here for live updates
- `POST /webhooks/twilio/status` — Twilio calls this on every delivery state change

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

---

## 6. Next.js Dispatcher Console

```
/app
  alerts/[id]/page.tsx     # main live console
  components/
    HouseholdStatusGrid.tsx  # live grid: household x channel x status
    AlertComposer.tsx        # form to draft + dispatch a new alert
    DeliveryTimeline.tsx     # per-household attempt history incl. fallback events
  hooks/
    useAlertSocket.ts        # WebSocket hook, reconnect logic, message reducer
```

- On mount: `GET /alerts/{id}/status` for initial snapshot, then open WebSocket for live diffs — avoids a blank screen while the socket connects.
- State: reducer keyed by `household_id` so incoming `delivery_update` events patch just one row instead of re-rendering the whole grid.
- Visually distinguish: delivered (green), failed→rerouted (amber, shows "SMS failed → retrying via Voice"), unreached after all fallbacks (red, needs human follow-up).
- Reconnect with backoff if the socket drops — a dispatcher console silently going stale during a disaster is the worst failure mode.

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
```

---

## Open Design Decisions to Make Early

- **ASL delivery**: pre-recorded human interpreter clips per template message vs. AI avatar generation — affects scope a lot. Starting with a small library of pre-recorded common-phrase clips + Claude only writing captions is far more buildable than generating video.
- **Confirmation of safety**: do you want two-way interaction (press 1 to confirm safe) in v1, or just delivery confirmation? Two-way is a strong differentiator but adds IVR complexity.
- **Zone lookup**: simple zone_id field vs. real geofencing — geofencing is a nice-to-have, not needed for the core loop.
