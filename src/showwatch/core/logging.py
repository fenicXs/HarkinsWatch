"""structlog JSON logging with secret redaction. Standard-library records go through the same pipeline."""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "***"
SENSITIVE_SUFFIXES = ("token", "secret", "password", "api_key")
SENSITIVE_NAMES = frozenset({"authorization", "cookie", "set-cookie", "dsn", "database_url"})

# Text patterns, applied to every string value (including formatted tracebacks):
_BOT_TOKEN_IN_URL = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_BARE_BOT_TOKEN = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@")


def _is_sensitive_key(key: str) -> bool:
    """Whole-name match, case-insensitive: the key equals a known name or ends with a sensitive word."""
    lowered = key.lower()
    return lowered in SENSITIVE_NAMES or lowered.endswith(SENSITIVE_SUFFIXES)


def _redact_text(text: str) -> str:
    text = _BOT_TOKEN_IN_URL.sub("bot<redacted>", text)
    text = _BARE_BOT_TOKEN.sub("<redacted-token>", text)
    return _URL_CREDENTIALS.sub("<redacted>@", text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive_key(k) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(v) for v in value]
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask values of sensitive keys, and tokens or URL credentials that appear inside text."""
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
        # Render exception tracebacks to text first, so redaction sees (and masks) them.
        structlog.processors.format_exc_info,
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
