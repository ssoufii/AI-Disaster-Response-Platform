"""Test fixtures.

Tests run against an in-memory SQLite database with the app's session
dependency overridden, so no test needs a live PostgreSQL instance. Twilio is
replaced wholesale in every test by the autouse ``twilio`` fixture below, and
the Anthropic client is replaced by whichever test exercises generation — no
test reaches either live API (CLAUDE.md, Testing).
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.models  # noqa: F401  registers every table on SQLModel.metadata
from app.config import settings
from app.db import get_session
from app.main import app
from app.services import delivery_service

PUBLIC_BASE_URL = "https://disaster-response.test"
TWILIO_PHONE_NUMBER = "+15550000000"


class SentMessage:
    """A message Twilio was asked to send, and the SID it answered with."""

    def __init__(self, sid: str, params: dict[str, Any]) -> None:
        self.sid = sid
        self.params = params


class FakeMessages:
    """Twilio's ``client.messages``, recording instead of sending.

    Set ``error`` to make the next send raise, which is how a test exercises the
    "Twilio refused the send" path without a network.
    """

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.error: Exception | None = None

    async def create_async(self, **params: Any) -> SentMessage:
        if self.error is not None:
            raise self.error
        message = SentMessage(sid=f"SM{len(self.sent):032d}", params=params)
        self.sent.append(message)
        return message


class FakeTwilio:
    def __init__(self) -> None:
        self.messages = FakeMessages()


@pytest.fixture(autouse=True)
def twilio(monkeypatch: pytest.MonkeyPatch) -> FakeTwilio:
    """Stand in for the Twilio client in every test.

    Autouse because CLAUDE.md's rule admits no exceptions: no test hits a live
    API. A test that never mentions this fixture is still covered by it.
    """
    fake = FakeTwilio()
    monkeypatch.setattr(delivery_service, "get_client", lambda: fake)
    # Plausible Twilio config, so tests assert against a real callback URL
    # rather than the empty-string defaults a developer machine carries.
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", PUBLIC_BASE_URL)
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", TWILIO_PHONE_NUMBER)
    return fake


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
