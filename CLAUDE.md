# CLAUDE.md

Guidance for Claude Code when working in this repository.

---

## Project

**AI Disaster Response Platform** — a disaster alert system that reaches households through
voice calls, ASL video, low-literacy SMS, or native-language outreach. The Claude API generates
alert content tailored to each household; Twilio delivers it; a Next.js dispatcher console
monitors delivery over WebSockets and automatically reroutes failed deliveries to a fallback
channel.

**Stack:** FastAPI · Next.js (App Router, TypeScript) · Claude API · Twilio · WebSockets · PostgreSQL

Full architecture and data model: `docs/architecture.md`. Read it before making structural changes.

---

## Repo Layout

```
/backend
  app/
    main.py               # FastAPI app, router registration, WS mount
    config.py             # pydantic-settings, all env access goes here
    db.py                 # async engine + session dependency
    api/
      alerts.py           # POST /alerts, POST /alerts/{id}/dispatch, GET /alerts/{id}/status
      households.py
      zones.py
    services/
      content_generator.py  # ALL Claude API calls
      delivery_service.py   # ALL Twilio calls + fallback rerouting
      dispatcher_ws.py      # WebSocket connection manager
    webhooks/
      twilio_status.py      # POST /webhooks/twilio/status
    models/                 # SQLModel table definitions
    schemas/                # Pydantic request/response models
  tests/
  alembic/
/frontend
  app/
    alerts/[id]/page.tsx    # live dispatcher console
  components/
  hooks/
    useAlertSocket.ts
  lib/
/docs
  architecture.md
```

---

## Commands

```bash
# Backend
cd backend
uv sync                                   # install deps
uv run uvicorn app.main:app --reload      # dev server :8000
uv run pytest                             # tests
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head
uv run ruff check . && uv run ruff format .

# Frontend
cd frontend
npm run dev                               # :3000
npm run build
npm run lint
npx tsc --noEmit                          # typecheck

# Local Twilio webhooks
ngrok http 8000                           # set PUBLIC_BASE_URL to the ngrok URL
```

---

## Domain Rules (do not violate these)

These are the rules that make this a *disaster response* system rather than a generic
notification app. They matter more than style preferences.

1. **Claude never invents facts.** Shelter addresses, evacuation routes, road closures, times,
   and phone numbers are passed in as structured fields from the dispatcher's alert. Claude
   rewrites *form* — reading level, language, channel format — never content. If a generation
   returns a fact not present in the input, that is a bug, not a prompt-tuning issue.

2. **Every delivery attempt is a row.** Never mutate a `DeliveryAttempt` to represent a retry.
   A fallback creates a *new* attempt with an incremented `attempt_number`. The console's audit
   trail depends on this.

3. **Fallback is webhook-driven, not polled.** Rerouting is triggered from the Twilio status
   webhook handler as an async task. Do not add a polling loop or cron job for this.

4. **A household is never silently dropped.** When all channels in
   `fallback_channel_order` are exhausted, mark the household `unreached` and emit a WebSocket
   event flagging it for human follow-up. Failing silently is the worst possible outcome here.

5. **Severity gates behavior, not just wording.** `evacuate_now` alerts skip any batching or
   rate-limit delay that lower severities may use.

6. **Delivery ≠ receipt.** `delivered` means Twilio handed it off. `confirmed_received` only
   comes from an explicit human action (DTMF keypress on a voice call, or an SMS reply).
   Never conflate them in code or UI.

---

## Backend Conventions

- **Async everywhere.** Async SQLAlchemy sessions, `httpx.AsyncClient`, async route handlers.
  No blocking I/O in request paths.
- **SQLModel** for table models in `app/models/`; separate Pydantic schemas in `app/schemas/`
  for request/response. Never return ORM objects directly from routes.
- **Service boundary is strict:**
  - Every Anthropic call goes through `services/content_generator.py`.
  - Every Twilio call goes through `services/delivery_service.py`.
  - No SDK client instantiated inside a route handler, ever.
- **Config via `app/config.py`** using pydantic-settings. No bare `os.getenv` outside that file.
- **Errors:** raise domain exceptions from services; translate to HTTP in an exception handler
  in `main.py`. Services should not know about `HTTPException`.
- **Migrations:** any model change needs an Alembic revision in the same commit.
- **Logging:** structured (`structlog`), and every delivery-path log line includes
  `alert_id` and `household_id`. Never log full phone numbers — last 4 digits only.

---

## Claude API Integration

- Client lives in `services/content_generator.py`. Model ID from `config.CLAUDE_MODEL`,
  default `claude-sonnet-5` (fast and cheap enough for per-household fan-out; check
  https://docs.claude.com/en/docs/about-claude/models/overview before changing).
- **Structured output only.** Use the structured outputs feature / a strict JSON schema so
  the response validates against a Pydantic model. Do not parse free text with regex, and do
  not strip markdown fences as a fallback strategy — if it doesn't validate, retry once, then
  fail loudly.
- Expected response shape:
  ```json
  {
    "sms_text": "...",
    "voice_script": "...",
    "asl_video_caption": "...",
    "language_used": "es"
  }
  ```
- Prompt inputs are structured, not concatenated prose: severity, raw_message, target language,
  literacy level, accessibility needs, channel, and a `facts` object (shelter, routes, times).
- **Fan-out is bounded.** Generating for a zone means hundreds of concurrent calls — use
  `asyncio.gather` with a semaphore (`config.CLAUDE_CONCURRENCY`, default 10) and retry with
  exponential backoff on 429/529.
- **Cache the system prompt** with prompt caching — it's identical across every household in
  a dispatch and this is the single biggest cost lever.
- **Never let a Claude failure block delivery.** If generation fails after retries, fall back
  to a pre-written template for that severity + language and log it prominently. A generic
  correct warning beats no warning.
- Prompts live in `services/prompts/` as versioned constants, not inline f-strings.

---

## Twilio Integration

- All sends go through `delivery_service.py`. Every send sets a `statusCallback` pointing at
  `{PUBLIC_BASE_URL}/webhooks/twilio/status`.
- **Validate webhook signatures** with `RequestValidator` on every inbound request. Non-negotiable
  — this endpoint mutates delivery state and is publicly reachable.
- Webhooks must be **idempotent**; Twilio retries. Key on `twilio_sid` + status.
- Return 200 fast: persist, enqueue the fallback task, return. Do not do Claude generation or
  outbound sends inline in the webhook handler.
- Channel map: `voice` → Programmable Voice/TwiML with `<Gather>` for confirmation;
  `sms` → Programmable Messaging; `video`/ASL → WhatsApp or MMS with a hosted clip link.
- Terminal failure statuses that trigger fallback: `failed`, `undelivered`, `no-answer`, `busy`.
  `queued`/`sending`/`ringing` are in-flight — never trigger fallback on these.

---

## WebSocket Contract

Endpoint: `WS /ws/alerts/{alert_id}`

Server → client:
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
  "timestamp": "2026-01-01T00:00:00Z"
}
```
Other `type` values: `dispatch_started`, `household_unreached`, `dispatch_complete`.

- Console loads `GET /alerts/{id}/status` for a snapshot **first**, then opens the socket for
  diffs. Never render a blank screen while the socket connects.
- Every event carries enough state to be applied standalone — clients must not depend on
  having seen prior events.
- If the schema changes, update `dispatcher_ws.py`, the TS types in `frontend/lib/types.ts`,
  and `docs/architecture.md` together.

---

## Frontend Conventions

- Next.js App Router, TypeScript strict mode, Tailwind. Server components by default;
  `"use client"` only where interactivity or the socket requires it.
- Live state lives in a `useReducer` keyed by `household_id` so an incoming update patches one
  row instead of re-rendering the grid.
- `useAlertSocket.ts` owns reconnect with exponential backoff, and surfaces a visible
  "connection lost / reconnecting" banner. **A dispatcher console that silently goes stale
  during a disaster is a critical failure** — never fail quietly.
- Status colors are consistent and meaningful:
  green = delivered/confirmed, amber = failed but rerouting, red = unreached (needs a human),
  grey = in flight.
- Rerouting must be legible: show "SMS failed → retrying via Voice", not just a status flip.

---

## Testing

- Twilio and Anthropic clients are **always mocked** in tests. No test hits a live API.
- Required coverage before a channel is considered done:
  - fallback chain exhaustion → household marked `unreached` + event emitted
  - webhook idempotency (same payload twice → one state change)
  - invalid webhook signature → 403
  - Claude returns malformed JSON → template fallback used, delivery still attempted
- Seed data (`backend/scripts/seed.py`) must include households spanning: low-literacy English,
  Spanish voice, ASL/video, a non-English SMS, and one guaranteed-to-fail number for exercising
  the fallback path.

---

## Environment

```
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5
CLAUDE_CONCURRENCY=10
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_WHATSAPP_NUMBER=
DATABASE_URL=
PUBLIC_BASE_URL=          # ngrok URL in dev; required for Twilio callbacks
CONSOLE_ORIGINS=          # comma-separated console origins allowed to read the API from the browser
NEXT_PUBLIC_API_URL=
NEXT_PUBLIC_WS_URL=
```

Never commit `.env`. Keep `.env.example` in sync when adding a variable.

---

## Build Order

Work in this sequence. Do not start a later stage while an earlier one is unproven.

1. Models + migrations + seed script
2. `content_generator.py` producing valid JSON for one household
3. SMS end-to-end: dispatch → Twilio → status webhook → DB
4. WebSocket plumbing + console rendering live SMS status
5. Fallback rerouting (test with the failing seed number)
6. Voice channel + DTMF confirmation
7. ASL/video channel
8. Auth on the console, rate limiting, retry polish

---

## Open Decisions

Ask before implementing — these are unresolved:

- **ASL delivery:** pre-recorded interpreter clip library vs. generated avatar video.
  Default to the clip library unless told otherwise; it's dramatically smaller scope.
- **Two-way confirmation:** whether v1 includes IVR "press 1 if safe" or only tracks delivery.

---

## Notes

- This is a portfolio project. Readability and a clean commit history matter; clever
  abstractions do not. Prefer obvious code.
- Do not add dependencies without asking.
- When touching the delivery path, update `docs/architecture.md` in the same change.
