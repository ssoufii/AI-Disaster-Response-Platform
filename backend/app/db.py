"""Async database engine and session dependency."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

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
