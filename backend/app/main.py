"""FastAPI application: router registration and domain-exception translation."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import households, zones
from app.exceptions import NotFoundError
from app.logging_config import configure_logging

configure_logging()

app = FastAPI(title="AI Disaster Response Platform")

app.include_router(zones.router)
app.include_router(households.router)


@app.exception_handler(NotFoundError)
async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    """Translate domain not-found errors into 404s.

    Services and routes raise domain exceptions; only this layer knows HTTP.
    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
