"""A small client for OpenAI-compatible chat APIs, with ordered fallback between endpoints.

Each task names its own endpoints in settings (primary first, then one fallback). A call tries them
in order and returns the first usable answer together with the endpoint that gave it, because
the vision task needs that endpoint's coordinate convention. The API key is only ever put in
the request header: it is never logged or put in an error message.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog
from pydantic import SecretStr

from showwatch.core.settings import Settings

log = structlog.get_logger(__name__)


class LLMError(Exception):
    """No endpoint produced an answer."""


@dataclass(frozen=True)
class Endpoint:
    name: str  # "primary" or "fallback": safe to log
    base_url: str
    model: str
    api_key: SecretStr
    timeout_s: float
    coord_order: Literal["yx", "xy"] = "yx"
    coord_scale: int = 1000


@dataclass(frozen=True)
class Completion:
    text: str
    endpoint: Endpoint


def vision_endpoints(settings: Settings) -> list[Endpoint]:
    """The configured seat-vision endpoints, primary first. Empty when none is configured."""
    found: list[Endpoint] = []
    if (
        settings.llm_vision_base_url
        and settings.llm_vision_model
        and settings.llm_vision_api_key is not None
    ):
        found.append(
            Endpoint(
                "primary",
                settings.llm_vision_base_url,
                settings.llm_vision_model,
                settings.llm_vision_api_key,
                settings.llm_vision_timeout_s,
                settings.llm_vision_coord_order,
                settings.llm_vision_coord_scale,
            )
        )
    if (
        settings.llm_vision_fallback_base_url
        and settings.llm_vision_fallback_model
        and settings.llm_vision_fallback_api_key is not None
    ):
        found.append(
            Endpoint(
                "fallback",
                settings.llm_vision_fallback_base_url,
                settings.llm_vision_fallback_model,
                settings.llm_vision_fallback_api_key,
                settings.llm_vision_fallback_timeout_s,
                settings.llm_vision_fallback_coord_order,
                settings.llm_vision_fallback_coord_scale,
            )
        )
    return found


class LLMClient:
    def __init__(
        self, endpoints: Sequence[Endpoint], *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._endpoints = list(endpoints)
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._endpoints)

    async def complete(
        self, messages: list[dict[str, Any]], *, max_tokens: int = 600
    ) -> Completion:
        """Ask each endpoint in turn; return the first non-empty answer, else raise `LLMError`."""
        if not self._endpoints:
            raise LLMError("no LLM endpoint is configured")
        reasons: list[str] = []
        for endpoint in self._endpoints:
            try:
                text = await self._ask(endpoint, messages, max_tokens)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
                reason = f"{endpoint.name}: {type(error).__name__}"
            else:
                if text.strip():
                    return Completion(text, endpoint)
                reason = f"{endpoint.name}: empty answer"
            log.warning("llm_endpoint_failed", reason=reason)
            reasons.append(reason)
        raise LLMError("; ".join(reasons))

    async def _ask(
        self, endpoint: Endpoint, messages: list[dict[str, Any]], max_tokens: int
    ) -> str:
        body = {
            "model": endpoint.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            # Thinking models can spend the whole token budget thinking and answer nothing.
            "reasoning_effort": "none",
        }
        headers = {"Authorization": f"Bearer {endpoint.api_key.get_secret_value()}"}
        url = endpoint.base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=endpoint.timeout_s, transport=self._transport) as http:
            response = await http.post(url, headers=headers, json=body)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else ""
