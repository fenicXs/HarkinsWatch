"""One Settings class for every process. Keys mirror .env.example."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: a blank `KEY=` line (as in .env.example) means "not set", not "".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Database
    database_url: str
    test_database_url: str | None = None

    # Telegram (required by the bot process only; see require_bot)
    telegram_bot_token: SecretStr | None = None
    admin_telegram_user_id: int | None = None

    log_level: str = "INFO"

    # Scheduling
    check_interval_s: int = 1800
    fast_interval_s: int = 60
    fast_window_h: int = 72
    poll_jitter_pct: int = 20
    job_lock_timeout_s: int = 900
    chain_request_budget_per_min: int = 10

    # LLM: seat-map vision (primary and one fallback)
    llm_vision_base_url: str | None = None
    llm_vision_model: str | None = None
    llm_vision_api_key: SecretStr | None = None
    llm_vision_timeout_s: int = 45
    llm_vision_coord_order: Literal["yx", "xy"] = "yx"
    llm_vision_coord_scale: int = 1000
    llm_vision_fallback_base_url: str | None = None
    llm_vision_fallback_model: str | None = None
    llm_vision_fallback_api_key: SecretStr | None = None
    llm_vision_fallback_timeout_s: int = 45
    llm_vision_fallback_coord_order: Literal["yx", "xy"] = "yx"
    llm_vision_fallback_coord_scale: int = 1000

    # LLM: text tasks (intent parsing, title matching), primary and one fallback
    llm_text_base_url: str | None = None
    llm_text_model: str | None = None
    llm_text_api_key: SecretStr | None = None
    llm_text_timeout_s: int = 45
    llm_text_fallback_base_url: str | None = None
    llm_text_fallback_model: str | None = None
    llm_text_fallback_api_key: SecretStr | None = None
    llm_text_fallback_timeout_s: int = 45

    def require_bot(self) -> tuple[SecretStr, int]:
        """Return the bot token and admin user id, or raise if either is missing."""
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
                ("ADMIN_TELEGRAM_USER_ID", self.admin_telegram_user_id),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"Missing required settings for the bot: {', '.join(missing)}")
        assert self.telegram_bot_token is not None
        assert self.admin_telegram_user_id is not None
        return self.telegram_bot_token, self.admin_telegram_user_id


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
