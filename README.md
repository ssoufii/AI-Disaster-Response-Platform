# AI Disaster Response Platform

A disaster alert system that reaches every household the way that household can actually
receive it: a voice call, an ASL video clip, a plain-language SMS, or a message in its own
language.

A dispatcher drafts one alert for a zone. The Claude API rewrites it for each household. Twilio
delivers it. A live console shows every delivery as it happens, and a delivery that fails is
automatically rerouted to the household's next channel.

**[Watch the console demo →](https://ssoufii.github.io/AI-Disaster-Response-Platform/demo/)**
The demo is a scripted playback that runs entirely in the browser. It uses the real console
components but needs no backend.

**Stack:** FastAPI · Next.js (App Router, TypeScript) · Claude API · Twilio · WebSockets · PostgreSQL

## How it works

```
Dispatcher drafts alert ──► Claude rewrites it per household ──► Twilio sends it
                                (form only, never facts)               │
                                                                       ▼
Console ◄── WebSocket ◄── status webhook ◄─────────────── delivered / failed
                                │
                                └─ failed? ──► new attempt on the next channel
                                                 (SMS → voice → ASL video …)
                                               all channels failed? ──► "unreached",
                                                 flagged for a person
```

- **Personalized content, never invented facts.** Shelter addresses, routes, and times come
  from the dispatcher as structured fields. Claude changes only reading level, language, and
  channel format. If Claude's output is malformed, a pre-written template goes out instead,
  because a generic correct warning beats no warning.
- **Every delivery attempt is its own row.** A fallback never overwrites the failed attempt,
  so the console's history is a true audit trail.
- **No household is dropped silently.** When every channel fails, the household is marked
  `unreached` and highlighted in red for human follow-up.
- **Delivered is not the same as received.** Only a human action (pressing 1 on a voice call)
  marks a household `confirmed_received`.
- **The console never goes stale quietly.** If the WebSocket drops, a banner says so, and the
  console re-reads the full status once it reconnects.

The full design is in [`docs/architecture.md`](docs/architecture.md), including the two v1
decisions on ASL delivery and IVR confirmation.
[`docs/backlog.md`](docs/backlog.md) lists the 20 stories the platform was built in.

## Running it locally

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22.18+, and PostgreSQL.
Twilio callbacks also need a public URL, for example from ngrok.

```bash
cp .env.example .env          # fill in Anthropic, Twilio, database, and the dispatcher token

# Backend: http://localhost:8000
cd backend
uv sync
uv run alembic upgrade head
uv run python scripts/seed.py  # demo households across every channel, plus one that always fails
uv run uvicorn app.main:app --reload

# Frontend: http://localhost:3000
cd frontend
npm install
npm run dev
```

Sign in to the console with the `DISPATCHER_API_TOKEN` from `.env`, then open
`/alerts/<alert id>`. `/demo` works without a backend.

## Tests

```bash
cd backend && uv run pytest     # Twilio and Anthropic are always mocked; tests use in-memory SQLite
cd frontend && npm test         # reducer, socket reconnect, auth, demo script
```

## Repo layout

```
backend/    FastAPI app, Alembic migrations, seed script, tests
frontend/   Next.js dispatcher console and the static demo
docs/       architecture spec and backlog
story_contexts/   the reasoning behind each backlog story
```
