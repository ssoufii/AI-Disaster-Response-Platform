"""structlog configuration.

Delivery-path log lines must carry ``alert_id`` and ``household_id`` and must
never contain a full phone number — see CLAUDE.md, Backend Conventions.
"""

import logging

import structlog


def redact_phone(phone_number: str) -> str:
    """Last 4 digits only — full numbers are never logged (CLAUDE.md).

    Lives here rather than in the delivery path so every caller that logs a
    household's number reaches for the same one-liner.
    """
    return f"***{phone_number[-4:]}"


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
