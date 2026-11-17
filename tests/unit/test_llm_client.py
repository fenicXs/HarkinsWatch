"""The LLM client: routing, fallback, timeouts, and that the key never leaks."""

import json

import httpx
import pytest
from pydantic import SecretStr

from showwatch.core.settings import Settings
from showwatch.llm.client import Endpoint, LLMClient, LLMError, vision_endpoints

KEY = "sk_" + "z" * 24
MESSAGES = [{"role": "user", "content": "hi"}]


def endpoint(name: str = "primary", base: str = "https://a.test/v1") -> Endpoint:
    return Endpoint(name, base, f"model-{name}", SecretStr(KEY), 5.0)


def reply(text: str | None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


async def test_the_first_endpoint_answers_and_the_request_is_well_formed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply("hello")

    client = LLMClient([endpoint()], transport=httpx.MockTransport(handler))
    completion = await client.complete(MESSAGES, max_tokens=50)

    assert completion.text == "hello"
    assert completion.endpoint.name == "primary"
    request = seen[0]
    assert str(request.url) == "https://a.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "model-primary"
    assert body["max_tokens"] == 50
    assert body["reasoning_effort"] == "none"  # a thinking model must not spend the budget thinking


@pytest.mark.parametrize(
    "first",
    [httpx.Response(500), httpx.Response(200, json={"nope": 1}), reply(None), reply("   ")],
)
async def test_a_failing_or_empty_primary_falls_back(first: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return first if request.url.host == "a.test" else reply("from fallback")

    client = LLMClient(
        [endpoint(), endpoint("fallback", "https://b.test/v1")],
        transport=httpx.MockTransport(handler),
    )
    completion = await client.complete(MESSAGES)
    assert (completion.text, completion.endpoint.name) == ("from fallback", "fallback")


async def test_a_timeout_falls_back_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            raise httpx.ReadTimeout("slow", request=request)
        return reply("ok")

    client = LLMClient(
        [endpoint(), endpoint("fallback", "https://b.test/v1")],
        transport=httpx.MockTransport(handler),
    )
    assert (await client.complete(MESSAGES)).endpoint.name == "fallback"


async def test_when_every_endpoint_fails_the_error_names_them_but_never_the_key() -> None:
    client = LLMClient(
        [endpoint(), endpoint("fallback", "https://b.test/v1")],
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text=f"boom {KEY}")),
    )
    with pytest.raises(LLMError) as raised:
        await client.complete(MESSAGES)
    message = str(raised.value)
    assert "primary" in message and "fallback" in message
    assert KEY not in message


async def test_no_endpoint_is_an_error_and_reports_not_configured() -> None:
    client = LLMClient([])
    assert not client.configured
    with pytest.raises(LLMError):
        await client.complete(MESSAGES)


def test_endpoints_come_from_settings_primary_first_and_only_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(__import__("os").environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)
    base = {"_env_file": None, "database_url": "postgresql+asyncpg://x/y"}

    assert vision_endpoints(Settings(**base)) == []  # type: ignore[arg-type]

    partial = Settings(**base, llm_vision_base_url="https://a.test/v1")  # type: ignore[arg-type]
    assert vision_endpoints(partial) == []  # no model or key: not usable

    both = Settings(  # type: ignore[call-arg]
        **base,
        llm_vision_base_url="https://a.test/v1",
        llm_vision_model="m1",
        llm_vision_api_key=SecretStr(KEY),
        llm_vision_fallback_base_url="https://b.test/v1",
        llm_vision_fallback_model="m2",
        llm_vision_fallback_api_key=SecretStr(KEY),
        llm_vision_fallback_coord_order="xy",
    )
    found = vision_endpoints(both)
    assert [(e.name, e.model, e.coord_order) for e in found] == [
        ("primary", "m1", "yx"),
        ("fallback", "m2", "xy"),
    ]


def test_text_endpoints_reuse_the_gateway_with_the_benchmarked_models() -> None:
    from pydantic import SecretStr

    from showwatch.core.settings import Settings
    from showwatch.llm.client import text_endpoints

    none = Settings(_env_file=None, database_url="postgresql+asyncpg://x")  # type: ignore[call-arg]
    assert text_endpoints(none) == []

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+asyncpg://x",
        llm_vision_base_url="https://gw.test/v1",
        llm_vision_api_key=SecretStr("k"),
    )
    primary, fallback = text_endpoints(settings)
    assert (primary.name, primary.base_url, primary.model) == (
        "primary",
        "https://gw.test/v1",
        "qwen36-35b-a3b",
    )
    assert (fallback.name, fallback.model) == ("fallback", "gemma4-31b-it")

    own = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+asyncpg://x",
        llm_text_base_url="https://other.test/v1",
        llm_text_api_key=SecretStr("k2"),
        llm_text_model="m1",
    )
    assert [(e.base_url, e.model) for e in text_endpoints(own)][0] == (
        "https://other.test/v1",
        "m1",
    )
