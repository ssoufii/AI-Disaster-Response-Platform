# AI Disaster Response Platform

A disaster alert system that reaches every household the way that household can actually
receive it: a voice call, an ASL video clip, a plain-language SMS, or a message in its own
language.

A dispatcher drafts one alert for a zone. The Claude API rewrites it for each household. Twilio
delivers it. A live console shows every delivery as it happens, and a delivery that fails is
automatically rerouted to the household's next channel.

**[Watch the live demo →](https://ssoufii.github.io/AI-Disaster-Response-Platform/)**
The demo is the real backend running in simulation mode: a mock alert goes out every couple of
minutes to 60 invented households, and the console shows each delivery, reroute and unreached
household as it happens. Nobody is texted or called, and no API is billed
([how](#live-demo-simulation-mode)).

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
`/alerts/<alert id>`.

## Live demo (simulation mode)

`SIMULATION_MODE=true` runs the whole platform for free, with no accounts and no secrets:

| | Real deployment | Simulation mode |
|---|---|---|
| Sending | Twilio | An in-process simulator. The Twilio client is never created. |
| Message text | Claude API | The pre-written templates (no API call) |
| Database | PostgreSQL | A SQLite file, migrated and seeded at startup |
| Dispatching | A dispatcher | A scheduler sends a mock alert every 2 minutes |
| Console | Needs the desk token | Read-only and public; changing anything still needs the token |

The delivery path itself is the production code. The simulator only reports outcomes, as signed
callbacks to the same webhooks Twilio calls, so rerouting, the `unreached` flag and the live
feed all run for real. Details: `docs/architecture.md`, section 9.

Run it locally:

```bash
cd backend
SIMULATION_MODE=true DATABASE_URL=sqlite+aiosqlite:///./demo.db \
  CONSOLE_ORIGINS=http://localhost:3000 uv run uvicorn app.main:app

cd frontend
NEXT_PUBLIC_LIVE_DEMO=1 NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

### Hosting the demo for free

The backend is one container (`backend/Dockerfile`) that needs no database or keys. On
[Koyeb](https://www.koyeb.com)'s free tier:

1. Create a **Web Service** from this GitHub repo. Builder: **Dockerfile**. Work directory:
   `backend`. Instance: **Free**. Port: `8000`. Health check path: `/health`.
2. Add one environment variable: `CONSOLE_ORIGINS=https://ssoufii.github.io`.
3. Deploy, and copy the service's public URL (`https://<name>.koyeb.app`).
4. In this repo: Settings → Secrets and variables → Actions → **Variables** → add
   `DEMO_API_URL` with that URL (no trailing slash).
5. Run the **Deploy demo to GitHub Pages** workflow. The published console now connects to
   the backend.

Any host that runs a Dockerfile works the same way (Render's free web service, for one). A
free instance may sleep when idle; the console says "Waking up the demo server" until it is back.

## Tests

```bash
cd backend && uv run pytest     # Twilio and Anthropic are always mocked; tests use in-memory SQLite
cd frontend && npm test         # reducer, socket reconnect, auth, console totals (Node 22+)
```

## Repo layout

```
backend/    FastAPI app, Alembic migrations, seed script, tests
frontend/   Next.js dispatcher console (also built as the public demo page)
docs/       architecture spec and backlog
story_contexts/   the reasoning behind each backlog story
```
