"""Application settings.

Every environment variable read in this codebase goes through here — no bare
``os.getenv`` anywhere else (see CLAUDE.md, Backend Conventions).
"""

from functools import lru_cache

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

    @property
    def console_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CONSOLE_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
