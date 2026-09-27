"""structlog configuration.

Delivery-path log lines must carry ``alert_id`` and ``household_id`` and must
never contain a full phone number — see CLAUDE.md, Backend Conventions.

The phone-number rule is enforced twice, on purpose:

1. Call sites that mean to log a number call ``redact_phone`` themselves, which
   is how the intent stays visible where the line is written.
2. ``redact_phone_numbers`` is a processor on every log line from every module,
   which is how the rule survives the call site that forgets — or never knew it
   was logging a number at all. The common case is not a careless ``log.info``:
   it is ``error=str(exc)``, where a provider's own message is quoting the number
   back at us ("The 'To' number +15555550123 is not valid"). No audit of call
   sites catches that one, because the number is not in the source.
"""

import logging
import re
from typing import Any

import structlog

# An E.164 number, with or without a transport scheme in front of it.
#
# The leading ``+`` is required, and that is the whole reason this can be applied
# to every field of every log line without mangling anything: a Twilio SID is a
# long run of hex and digits, and matching bare digit runs would redact half of
# one. Every number this system holds is E.164 — Twilio accepts nothing else —
# so requiring the ``+`` costs no coverage.
#
# Deliberately no upper bound on the digits. E.164 allows at most 15, but a
# pattern that stopped there would match nothing at all in a *longer* run — and
# the thing most likely to be longer is a malformed number someone typed, which
# is exactly the one that must not reach a log intact. A long digit run after a
# ``+`` is a phone number by any reading worth having here.
PHONE_PATTERN = re.compile(r"(?<!\d)(?P<scheme>whatsapp:)?\+\d{6,}")


def redact_phone(phone_number: str) -> str:
    """Last 4 digits only — full numbers are never logged (CLAUDE.md).

    Lives here rather than in the delivery path so every caller that logs a
    household's number reaches for the same one-liner.
    """
    return f"***{phone_number[-4:]}"


def _redact_in_text(text: str) -> str:
    """Every phone number in one string, reduced to its last 4 digits.

    The scheme is kept when there is one: ``whatsapp:***0123`` still says which
    transport the send was on, which is worth having in a log and gives nothing
    away.
    """
    return PHONE_PATTERN.sub(
        lambda match: f"{match.group('scheme') or ''}{redact_phone(match.group(0))}",
        text,
    )


def _redacted(value: Any) -> Any:
    """``value`` with any phone number inside it redacted.

    Recurses into the containers a structlog event can actually hold — a bound
    dict of lists of strings — and passes everything else through untouched. A
    value this does not understand is returned as it came rather than
    stringified: rendering is the renderer's job, and doing it here would change
    what every log line looks like.
    """
    if isinstance(value, str):
        return _redact_in_text(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        redacted = [_redacted(item) for item in value]
        return tuple(redacted) if isinstance(value, tuple) else redacted
    return value


def redact_phone_numbers(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Strip full phone numbers out of every log line, wherever they turn up.

    The backstop for CLAUDE.md's redaction rule. It runs over the whole event —
    the message as well as the bound fields, since ``event`` is just another key
    — so a number reaches the log redacted whether a call site remembered or not.
    """
    return {key: _redacted(value) for key, value in event_dict.items()}


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # Last before rendering, so it sees every field every other processor
            # has already added — including the ones this module never wrote.
            redact_phone_numbers,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
