import json
import logging

import pytest
import structlog

from showwatch.core.logging import REDACTED, configure_logging, redact_processor


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


def test_dedupe_key_is_not_masked() -> None:
    out = _redact(event="x", dedupe_key="new_release:7")
    assert out["dedupe_key"] == "new_release:7"


def test_masks_bot_token_inside_text() -> None:
    url = "POST https://api.telegram.org/bot123456:ABC-def_GHI/sendMessage"
    out = _redact(event=url)
    assert "123456:ABC" not in str(out["event"])
    assert "bot<redacted>" in str(out["event"])


def test_masks_nested_dict_values() -> None:
    out = _redact(event="x", headers={"authorization_token": "t", "accept": "json"})
    assert out["headers"] == {"authorization_token": REDACTED, "accept": "json"}


def test_configured_logger_emits_json_without_the_token(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("test").info("hello", api_key="secret-value")
    logging.getLogger("lib").warning("GET https://api.telegram.org/bot99:TOKEN/getMe")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0]["event"] == "hello"
    assert lines[0]["api_key"] == REDACTED
    assert "TOKEN" not in lines[1]["event"]
