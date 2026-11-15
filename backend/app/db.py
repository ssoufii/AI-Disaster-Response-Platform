"""Async database engine and session dependency."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

# SQLite is what the live simulation demo runs on (an embedded file, so the demo
# needs no hosted database). It allows one writer at a time, and a dispatch has
# many webhooks writing at once, so a writer waits for its turn instead of
# failing with "database is locked", and WAL lets readers carry on meanwhile.
IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")
SQLITE_BUSY_TIMEOUT_SECONDS = 30

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS} if IS_SQLITE else {},
)

if IS_SQLITE:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async session per request."""
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """A session for work that has no request to borrow one from.

    Background work — the fallback reroute the status webhook schedules (#12) —
    outlives the request that started it, so it cannot use that request's
    session: by the time the task runs, the webhook has already answered Twilio
    and its session is closed.
    """
    async with async_session_factory() as session:
        yield session
