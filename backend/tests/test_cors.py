"""The console's origin is allowed to read the API from the browser.

Not a style detail: after a dropped socket the console resyncs by fetching
``GET /alerts/{id}/status`` from the browser, and a blocked resync leaves it
unable to prove it is current — stuck behind a "connection lost" banner it can
never clear. Everything the console did before that (the server-rendered
snapshot, the WebSocket) sidesteps CORS, so this endpoint is the first one that
needs it.
"""

import pytest
from httpx import AsyncClient

from app.config import settings

CONSOLE_ORIGIN = "http://localhost:3000"


@pytest.mark.anyio
async def test_console_origin_may_read_the_status_endpoint(client: AsyncClient) -> None:
    response = await client.get(
        "/alerts/00000000-0000-0000-0000-000000000000/status",
        headers={"Origin": CONSOLE_ORIGIN},
    )

    # The alert does not exist — a 404 is expected and beside the point. What
    # matters is that the browser is allowed to read the answer either way.
    assert response.headers["access-control-allow-origin"] == CONSOLE_ORIGIN


@pytest.mark.anyio
async def test_an_unlisted_origin_gets_no_allowance(client: AsyncClient) -> None:
    response = await client.get(
        "/alerts/00000000-0000-0000-0000-000000000000/status",
        headers={"Origin": "http://not-the-console.example"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_the_default_console_origin_is_the_dev_console_never_a_wildcard() -> None:
    # Read off the field rather than the live settings, so a developer's own
    # .env does not decide whether this passes.
    default = type(settings).model_fields["CONSOLE_ORIGINS"].default

    assert default == CONSOLE_ORIGIN
    assert "*" not in default


def test_console_origins_splits_a_comma_separated_list() -> None:
    parsed = type(settings)(
        CONSOLE_ORIGINS="http://localhost:3000, https://console.example "
    ).console_origins

    assert parsed == ["http://localhost:3000", "https://console.example"]
