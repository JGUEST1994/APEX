"""Environment-based configuration.

All settings come from environment variables prefixed ``APEX_`` (or a ``.env`` file).
Secrets are never hard-coded; ``SecretStr`` keeps them out of reprs and logs.

Safety: ``TradingMode`` has no LIVE member in this release. Real-money trading cannot be
switched on by configuration; it would require new code that passes review.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    RESEARCH = "research"  # data, research, backtests only; no order routing at all
    PAPER = "paper"  # simulated execution only


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEX_", env_file=".env", extra="ignore")

    env: Environment = Environment.DEVELOPMENT
    database_url: str = "sqlite:///./apex.db"
    trading_mode: TradingMode = TradingMode.RESEARCH
    log_level: str = "INFO"
    log_json: bool = False
    stale_data_seconds: int = Field(default=180, ge=1)

    # Market data credentials (optional; only needed for the matching provider)
    alpaca_key_id: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    alpaca_feed: str = "iex"
    polygon_api_key: SecretStr | None = None
    tiingo_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None  # free key from fred.stlouisfed.org (FRED/ALFRED)
    sec_user_agent: str = ""  # "Your Name you@example.com", required by the SEC for EDGAR

    # Optional access token for the API and dashboard. Unset = no token (keep it on localhost).
    api_token: SecretStr | None = None
    # Host names the API answers to. Anything else gets 400, so a web page that points its own
    # name at 127.0.0.1 ("DNS rebinding") cannot read the dashboard. Add your server's name here
    # (APEX_ALLOWED_HOSTS='["127.0.0.1","localhost","apex.example.com"]') only if you host APEX.
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    # FastAPI's /docs and /redoc pages load scripts from a public CDN; off unless asked for.
    api_docs: bool = False

    # Optional external AI; "none" means fully disabled. Core never depends on it.
    ai_provider: str = "none"
    ai_api_key: SecretStr | None = None

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _reject_live(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().lower() in {"live", "real", "production"}:
            raise ValueError(
                "Live/real-money trading is not available in this release of APEX. "
                "Use 'research' or 'paper'."
            )
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
