"""FastAPI application: router registration and domain-exception translation."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import alerts, households, zones
from app.config import settings
from app.exceptions import NotFoundError
from app.logging_config import configure_logging
from app.services import dispatcher_ws
from app.webhooks import twilio_status

configure_logging()

app = FastAPI(title="AI Disaster Response Platform")

# The console reads GET /alerts/{id}/status from the browser when it resyncs
# after a dropped socket, and it is served from its own origin. Without this the
# resync is blocked and the console can never clear its "reconnecting" banner —
# a console stuck looking broken, which is the failure this is here to avoid.
# Named origins and reads only: nothing here is a way in, and auth is #20's.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.console_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(zones.router)
app.include_router(households.router)
app.include_router(alerts.router)
app.include_router(twilio_status.router)
# WS /ws/alerts/{alert_id} — the dispatcher console's live feed.
app.include_router(dispatcher_ws.router)


@app.exception_handler(NotFoundError)
async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    """Translate domain not-found errors into 404s.

    Services and routes raise domain exceptions; only this layer knows HTTP.
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
