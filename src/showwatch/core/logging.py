"""structlog JSON logging with secret redaction. Standard-library records go through the same pipeline."""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "***"
SENSITIVE_SUFFIXES = ("token", "secret", "password", "api_key")
_BOT_TOKEN_IN_TEXT = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def _is_sensitive_key(key: str) -> bool:
    """Whole-name match, case-insensitive: the key equals or ends with a sensitive word."""
    lowered = key.lower()
    return lowered.endswith(SENSITIVE_SUFFIXES)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _BOT_TOKEN_IN_TEXT.sub("bot<redacted>", value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive_key(k) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask values of sensitive keys and any Telegram bot token that appears inside text."""
    for key in list(event_dict):
        if _is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Their INFO lines contain request URLs, which include the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
