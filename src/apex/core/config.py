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
