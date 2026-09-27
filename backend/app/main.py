"""FastAPI application: router registration and domain-exception translation."""

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import alerts, households, zones
from app.auth import require_dispatcher
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
# Named origins and reads only. CORS is not the access control either — the
# dispatcher token below is — it only says which origin's JavaScript may read a
# reply the caller was already authorized to receive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.console_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Everything the console reads or writes is behind the dispatcher token, and it
# is attached per router rather than per handler so a route added later is
# guarded by having been added (app/auth.py explains what each router holds that
# is worth guarding).
CONSOLE_AUTH = [Depends(require_dispatcher)]

app.include_router(zones.router, dependencies=CONSOLE_AUTH)
app.include_router(households.router, dependencies=CONSOLE_AUTH)
app.include_router(alerts.router, dependencies=CONSOLE_AUTH)
# Deliberately unguarded: Twilio calls these from the public internet and cannot
# present our token. They authenticate every request by validating its
# X-Twilio-Signature instead, and refuse a forgery with a 403 before reading a
# row.
app.include_router(twilio_status.router)
# WS /ws/alerts/{alert_id} — the dispatcher console's live feed. Its own
# handshake check lives in the endpoint, because a WebSocket is refused by
# closing it rather than by raising an HTTP error.
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
