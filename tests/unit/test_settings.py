import pytest
from pydantic import ValidationError

from showwatch.core.settings import Settings

DSN = "postgresql+asyncpg://showwatch:pw@localhost:5433/showwatch"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "ADMIN_TELEGRAM_USER_ID"):
        monkeypatch.delenv(key, raising=False)


def test_defaults_match_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DSN)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.check_interval_s, s.fast_interval_s, s.fast_window_h) == (1800, 60, 72)
    assert s.poll_jitter_pct == 20
    assert s.job_lock_timeout_s == 900
    assert s.chain_request_budget_per_min == 10
    assert s.llm_vision_timeout_s == 45
    assert (s.llm_vision_coord_order, s.llm_vision_coord_scale) == ("yx", 1000)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_unknown_env_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")  # compose-only key
    Settings(_env_file=None)  # type: ignore[call-arg]


def test_require_bot_lists_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DSN)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_USER_ID"):
        s.require_bot()


def test_require_bot_returns_token_and_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_ID", "42")
    token, admin = Settings(_env_file=None).require_bot()  # type: ignore[call-arg]
    assert token.get_secret_value() == "123:abc"
    assert admin == 42
