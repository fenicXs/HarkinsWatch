import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from showwatch.core.logging import REDACTED, configure_logging, redact_processor

FAKE_TOKEN = "123456789:" + "A" * 35  # shaped like a Telegram bot token, not a real one


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """configure_logging replaces the root handlers; put things back for later tests."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def _redact(**event: object) -> dict[str, object]:
    return dict(redact_processor(None, "info", dict(event)))


def test_masks_values_of_sensitive_keys() -> None:
    out = _redact(event="x", telegram_bot_token="123:abc", db_password="pw", client_secret="s")
    assert out["telegram_bot_token"] == REDACTED
    assert out["db_password"] == REDACTED
    assert out["client_secret"] == REDACTED


def test_key_matching_is_case_insensitive() -> None:
    out = _redact(event="x", Telegram_Token="123:abc", API_KEY="k")
    assert out["Telegram_Token"] == REDACTED
    assert out["API_KEY"] == REDACTED


def test_masks_known_sensitive_names() -> None:
    out = _redact(
        event="x", Authorization="Bearer abc", cookie="sid=1", database_url="postgres://x"
    )
    assert out["Authorization"] == REDACTED
    assert out["cookie"] == REDACTED
    assert out["database_url"] == REDACTED


def test_masks_other_common_secret_key_names() -> None:
    out = _redact(event="x", passwd="p", apikey="k", aws_access_key="a", credentials="c", auth="b")
    assert all(
        out[key] == REDACTED
        for key in ("passwd", "apikey", "aws_access_key", "credentials", "auth")
    )


def test_dedupe_key_is_not_masked() -> None:
    out = _redact(event="x", dedupe_key="new_release:7")
    assert out["dedupe_key"] == "new_release:7"


def test_masks_bot_token_inside_a_url() -> None:
    url = "POST https://api.telegram.org/bot123456:ABC-def_GHI/sendMessage"
    out = _redact(event=url)
    assert "123456:ABC" not in str(out["event"])
    assert "bot<redacted>" in str(out["event"])


def test_masks_a_bare_bot_token_in_text() -> None:
    out = _redact(event=f"token is {FAKE_TOKEN} ok")
    assert FAKE_TOKEN not in str(out["event"])


def test_masks_credentials_inside_a_connection_string() -> None:
    out = _redact(event="connect postgresql+asyncpg://showwatch:hunter2@postgres:5432/db failed")
    assert "hunter2" not in str(out["event"])
    assert "postgres:5432/db" in str(out["event"])


def test_masks_nested_values_in_dicts_lists_and_sets() -> None:
    out = _redact(
        event="x",
        headers={"authorization_token": "t", "accept": "json"},
        items=[f"a {FAKE_TOKEN}", "b"],
        tags={"https://u:p@h/x"},
    )
    assert out["headers"] == {"authorization_token": REDACTED, "accept": "json"}
    assert FAKE_TOKEN not in str(out["items"])
    assert "u:p@" not in str(out["tags"])


def test_configured_logger_emits_json_without_the_token(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    structlog.get_logger("test").info("hello", api_key="secret-value")
    logging.getLogger("lib").warning("GET https://api.telegram.org/bot99:TOKEN/getMe")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0]["event"] == "hello"
    assert lines[0]["api_key"] == REDACTED
    assert "TOKEN" not in lines[1]["event"]


def test_exception_tracebacks_are_redacted(
    restore_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    log = structlog.get_logger("test")
    try:
        raise RuntimeError(f"request to https://api.telegram.org/bot{FAKE_TOKEN}/getMe failed")
    except RuntimeError:
        log.exception("boom")
    try:
        raise RuntimeError(f"stdlib failure {FAKE_TOKEN}")
    except RuntimeError:
        logging.getLogger("lib").exception("stdlib boom")
    out = capsys.readouterr().out
    assert FAKE_TOKEN not in out
    assert "RuntimeError" in out
