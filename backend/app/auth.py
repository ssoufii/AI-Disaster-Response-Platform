"""Who is allowed to read an alert, dispatch one, or watch one go out.

Everything the dispatcher console touches sits behind one shared token
(``config.DISPATCHER_API_TOKEN``). That is deliberately the smallest mechanism
that closes the hole: #20 puts user and role management out of scope, and the
people who hold this token are the people already trusted to order an
evacuation.

What it guards, and why each:

- ``/alerts/*`` — reading a dispatch tells you which households were warned and
  which were not, and ``POST /alerts/{id}/dispatch`` spends real money placing
  real calls to real people.
- ``/households`` and ``/zones`` — the household register *is* the PII. A story
  about not leaking phone numbers into logs that left them readable over an
  unauthenticated GET would be closing the smaller of two doors.
- ``WS /ws/alerts/{alert_id}`` — the socket streams every delivery state change
  as it happens, so an unauthenticated subscriber is an unauthenticated reader
  of the same data with a live feed.

What it deliberately does not guard:

- ``/webhooks/twilio/*``. Twilio reaches those from the public internet and
  cannot present a token of ours. They authenticate what they actually receive,
  by validating the ``X-Twilio-Signature`` on every request — a different
  mechanism for a different caller, not a gap (see ``webhooks/twilio_status.py``).
- ``/health``. It says whether the process is up and nothing else.

Two failure modes, kept distinct, because an operator reading a log during an
incident needs to tell them apart:

- A caller with a wrong or missing token is a **401**. Retrying with the right
  token will work.
- A deployment with no token configured is a **503**, and every request gets one.
  Nothing can be authenticated, so nothing is served: the alternative reading —
  no token means no check — is an open console on whichever deployment forgot the
  variable. Retrying will not help; someone has to set it. This mirrors the
  webhook's refusal to validate signatures without an auth token.
"""

import secrets

import structlog
from fastapi import HTTPException, Request, WebSocket, status

from app.config import settings

logger = structlog.get_logger(__name__)

BEARER_PREFIX = "Bearer "

# Why the socket's token arrives in the query string: a browser's ``WebSocket``
# constructor takes a URL and nothing else — there is no way to set an
# ``Authorization`` header on the handshake it sends. The cost is that the token
# reaches proxy access logs, which is why this endpoint's own logging never
# echoes it back and the token is a revocable shared secret rather than a
# password anyone reuses.
SOCKET_TOKEN_PARAM = "token"

# RFC 6455's "policy violation": the handshake was well-formed and refused on
# its merits. Sent *instead of* accepting the connection, so a console that
# cannot authenticate is never a subscriber, not even briefly.
WS_UNAUTHORIZED_CODE = status.WS_1008_POLICY_VIOLATION


def _token_configured() -> str | None:
    """The configured dispatcher token, or ``None`` if there is not one."""
    token = settings.DISPATCHER_API_TOKEN.strip()
    return token or None


def _matches(presented: str) -> bool:
    """Whether a presented token is the configured one.

    ``compare_digest`` rather than ``==``: the comparison is against a secret,
    and a short-circuiting one leaks how much of a guess was right.

    Compared as bytes, because ``compare_digest`` refuses two ``str`` arguments
    when either holds a non-ASCII character — and the presented one is whatever
    a caller put in the header. Encoding first is what makes a token with an
    accent in it a 401 like any other wrong token, rather than a 500.
    """
    expected = _token_configured()
    if expected is None:
        return False
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _presented_token(authorization: str | None) -> str | None:
    """The bearer token out of an ``Authorization`` header, if it carries one."""
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        return None
    return authorization.removeprefix(BEARER_PREFIX).strip() or None


async def require_dispatcher(request: Request) -> None:
    """Refuse the request unless it carries the dispatcher token.

    Wired on as a router-level dependency in ``main.py``, so a route added to a
    guarded router is guarded by having been added — there is no per-handler line
    to forget. It returns nothing: there is one identity, and knowing the caller
    holds the token is the whole of what any route needs.
    """
    if _token_configured() is None:
        logger.error("dispatcher_auth.token_unconfigured", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatcher authentication is not configured",
        )

    presented = _presented_token(request.headers.get("Authorization"))
    if presented is None or not _matches(presented):
        # The presented token is never logged, whole or partial: a log line is
        # not the place to put a credential someone is trying. What an operator
        # needs is that an unauthenticated request arrived, and where.
        logger.warning(
            "dispatcher_auth.rejected",
            path=request.url.path,
            method=request.method,
            credential_presented=presented is not None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dispatcher token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def authorize_console_socket(websocket: WebSocket, token: str | None) -> bool:
    """Whether this socket may subscribe; closes it when the answer is no.

    Returns ``False`` having already closed the connection, so the caller's only
    job is to stop. The close happens *before* ``accept()``, which is what makes
    this a refused handshake rather than a subscriber that gets dropped a moment
    later — an unauthenticated console is never in the registry at all, so no
    ``delivery_update`` can reach it.
    """
    if _token_configured() is None:
        logger.error("dispatcher_auth.socket_token_unconfigured")
        await websocket.close(code=WS_UNAUTHORIZED_CODE)
        return False

    if token is None or not _matches(token):
        logger.warning("dispatcher_auth.socket_rejected", credential_presented=token is not None)
        await websocket.close(code=WS_UNAUTHORIZED_CODE)
        return False

    return True
