"""A scriptable LLM for tests: recorded replies, no network."""

from typing import Any

from pydantic import SecretStr

from showwatch.llm.client import Completion, Endpoint, LLMClient, LLMError

ENDPOINT = Endpoint("primary", "https://llm.test/v1", "test-model", SecretStr("x"), 5.0)


class FakeLLM(LLMClient):
    """Answers an intent request from `intents[text]` and a title match from `matches[query]`."""

    def __init__(
        self,
        intents: dict[str, str] | None = None,
        matches: dict[str, str] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        super().__init__([ENDPOINT])
        self.intents = intents or {}
        self.matches = matches or {}
        self.fail = fail
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], *, max_tokens: int = 600
    ) -> Completion:
        self.calls.append(messages)
        if self.fail:
            raise LLMError("primary: ConnectError")
        user = str(messages[-1]["content"])
        if user.startswith("User's words:"):
            query = user.removeprefix("User's words: ").split("\n", 1)[0]
            return Completion(self.matches.get(query, "[]"), ENDPOINT)
        return Completion(self.intents.get(user, "{}"), ENDPOINT)
