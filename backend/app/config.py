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
