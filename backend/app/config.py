"""Application settings.

Every environment variable read in this codebase goes through here — no bare
``os.getenv`` anywhere else (see CLAUDE.md, Backend Conventions).
"""

import secrets
from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Claude
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-5"
    CLAUDE_CONCURRENCY: int = 10

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""

    # Where the ASL interpreter clips are served from. The clips are not in the
    # repo — `services/asl_clips.py` holds the manifest, this says where the
    # files it names live — and the URL must be reachable from the public
    # internet, because Twilio fetches the media itself rather than being handed
    # it (docs/architecture.md, "Decision: ASL delivery").
    ASL_CLIP_BASE_URL: str = ""

    # The shared secret a dispatcher's console presents to read or dispatch an
    # alert. One token for the whole dispatch desk rather than per-user accounts:
    # #20's story puts user and role management out of scope, and a console
    # nobody can sign into during an incident is worse than a shared credential
    # held by the people already trusted to order an evacuation.
    #
    # Empty by default, and empty means *refuse everything* rather than let
    # everything through — see ``app/auth.py``. A deployment that forgot the
    # variable is a deployment whose console is closed, not one whose households'
    # phone numbers are public.
    DISPATCHER_API_TOKEN: str = ""

    # Infrastructure
    DATABASE_URL: str = "postgresql+asyncpg://localhost/disaster_response"
    PUBLIC_BASE_URL: str = ""

    # Where the dispatcher console is served from. The console is a separate
    # origin by design (that is what NEXT_PUBLIC_API_URL is for), so its
    # browser-side resync of GET /alerts/{id}/status needs this endpoint to say
    # the origin is allowed — otherwise a console that lost its socket can never
    # prove it is current again and sits behind a "reconnecting" banner forever.
    # Comma-separated; listed explicitly rather than wildcarded.
    CONSOLE_ORIGINS: str = "http://localhost:3000"

    # --- Live simulation demo (Epic 9) ------------------------------------
    #
    # One switch for the always-on public demo. With it on:
    #   - Twilio is replaced by ``services/twilio_simulator.py``; no real client
    #     is ever built, so nothing can be texted or called.
    #   - Claude is not called; content comes from the pre-written templates.
    #   - ``services/demo_scheduler.py`` dispatches a mock alert on a timer.
    #   - The console's read-only routes are public (``app/auth.py``).
    # Off (the default), none of that code runs.
    SIMULATION_MODE: bool = False

    # How long the simulator waits between one status and the next, in seconds.
    SIM_STEP_MIN_SECONDS: float = 2.0
    SIM_STEP_MAX_SECONDS: float = 9.0

    # How often each channel fails, and how often an answered call presses 1.
    SIM_SMS_FAILURE_RATE: float = 0.18
    SIM_VOICE_NO_ANSWER_RATE: float = 0.25
    SIM_CLIP_FAILURE_RATE: float = 0.2
    SIM_VOICE_CONFIRM_RATE: float = 0.6

    # A new mock alert this often, and a wipe-and-reseed once a day at this hour
    # (UTC) so the demo database never grows without bound.
    DEMO_DISPATCH_INTERVAL_MINUTES: float = 2.0
    DEMO_RESET_HOUR_UTC: int = 8

    @model_validator(mode="after")
    def _simulation_defaults(self) -> Self:
        """Give simulation mode the Twilio-side values it needs, and only fake ones.

        The auth token is *replaced*, not defaulted: the webhooks validate every
        callback against it, and in simulation mode the only thing that should
        be able to sign one is the simulator in this process. A random
        per-process secret guarantees that, even on a deployment that also
        carries real Twilio credentials.
        """
        if self.SIMULATION_MODE:
            self.TWILIO_AUTH_TOKEN = secrets.token_hex(32)
            self.TWILIO_ACCOUNT_SID = ""
            # Twilio's documented test "from" number; nothing is ever sent from it.
            self.TWILIO_PHONE_NUMBER = self.TWILIO_PHONE_NUMBER or "+15005550006"
            self.ASL_CLIP_BASE_URL = self.ASL_CLIP_BASE_URL or "https://clips.invalid/asl"
        return self

    @property
    def console_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CONSOLE_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
