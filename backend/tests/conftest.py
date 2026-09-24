"""Test fixtures.

Tests run against an in-memory SQLite database with the app's session
dependency overridden, so no test needs a live PostgreSQL instance. Twilio is
replaced wholesale in every test by the autouse ``twilio`` fixture below, and
the Anthropic client is replaced by whichever test exercises generation — no
test reaches either live API (CLAUDE.md, Testing).

The same fixture installs a stand-in Twilio auth token, which
``twilio_signed_headers`` uses to sign status callbacks the way Twilio does —
the webhook refuses anything unsigned.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.request_validator import RequestValidator

import app.models  # noqa: F401  registers every table on SQLModel.metadata
from app.config import settings
from app.db import get_session
from app.main import app
from app.services import delivery_service
from app.webhooks.twilio_status import SIGNATURE_HEADER

PUBLIC_BASE_URL = "https://disaster-response.test"
TWILIO_PHONE_NUMBER = "+15550000000"
# Not a credential: a stand-in token so tests can sign callbacks the way Twilio
# does. The real one only ever comes from the environment.
TWILIO_AUTH_TOKEN = "test-auth-token"
STATUS_CALLBACK_URL = f"{PUBLIC_BASE_URL}{delivery_service.STATUS_CALLBACK_PATH}"
GATHER_CALLBACK_URL = f"{PUBLIC_BASE_URL}{delivery_service.GATHER_CALLBACK_PATH}"


def twilio_signed_headers(
    params: dict[str, Any], *, token: str = TWILIO_AUTH_TOKEN, url: str = STATUS_CALLBACK_URL
) -> dict[str, str]:
    """Sign a status callback the way Twilio signs it.

    The overrides exist so a test can sign with the wrong token or the wrong URL
    and watch the endpoint refuse it.
    """
    signature = RequestValidator(token).compute_signature(url, params)
    return {SIGNATURE_HEADER: signature}


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


class PlacedCall:
    """A call Twilio was asked to place, and the SID it answered with."""

    def __init__(self, sid: str, params: dict[str, Any]) -> None:
        self.sid = sid
        self.params = params


class FakeCalls:
    """Twilio's ``client.calls``, recording instead of dialling.

    The voice twin of ``FakeMessages``, down to ``error``: a call Twilio refuses
    outright is the same path as a message it refuses, and a test can exercise
    it here without a phone line.
    """

    def __init__(self) -> None:
        self.placed: list[PlacedCall] = []
        self.error: Exception | None = None

    async def create_async(self, **params: Any) -> PlacedCall:
        if self.error is not None:
            raise self.error
        call = PlacedCall(sid=f"CA{len(self.placed):032d}", params=params)
        self.placed.append(call)
        return call


class FakeTwilio:
    def __init__(self) -> None:
        self.messages = FakeMessages()
        self.calls = FakeCalls()


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
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN)
    return fake


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Background work — the reroute the status webhook schedules — opens its
        # own session, because in production the request that scheduled it has
        # already closed hers. Here it is handed this test's session instead, so
        # the task reads the in-memory database the test wrote rather than
        # reaching for DATABASE_URL and a PostgreSQL instance no test has.
        @asynccontextmanager
        async def test_session_scope() -> AsyncGenerator[AsyncSession, None]:
            yield session

        monkeypatch.setattr(delivery_service, "session_scope", test_session_scope)
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
