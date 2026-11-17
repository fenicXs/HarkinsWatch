"""Seat vision: reading the model's points, converting conventions, retrying."""

import json

import httpx
from pydantic import SecretStr

from showwatch.llm.client import Endpoint, LLMClient
from showwatch.llm.tasks.seat_vision import find_points, parse_points


def test_a_plain_array_of_points_is_read() -> None:
    assert parse_points("[[500, 250], [510.5, 300]]") == [(500.0, 250.0), (510.5, 300.0)]


def test_surrounding_prose_and_code_fences_are_ignored() -> None:
    text = "Here you go:\n```json\n[[100, 200],\n [300, 400]]\n```\nHope that helps."
    assert parse_points(text) == [(100.0, 200.0), (300.0, 400.0)]


def test_x_first_models_are_swapped_to_y_first() -> None:
    assert parse_points("[[250, 500]]", order="xy") == [(500.0, 250.0)]


def test_other_scales_are_converted_to_a_thousand() -> None:
    assert parse_points("[[50, 25]]", scale=100) == [(500.0, 250.0)]
    assert parse_points("[[0.5, 0.25]]", scale=1) == [(500.0, 250.0)]


def test_bad_entries_and_out_of_range_points_are_dropped() -> None:
    text = '[[1, 2], [5], "x", [true, 3], ["a", "b"], [1500, 10], [-5, 10], [10, 20]]'
    assert parse_points(text) == [(1.0, 2.0), (10.0, 20.0)]


def test_text_without_an_array_gives_no_points() -> None:
    assert parse_points("I cannot see the image") == []
    assert parse_points("[[1, 2], broken") == []
    assert parse_points("") == []


class Sleeper:
    def __init__(self) -> None:
        self.pauses: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.pauses.append(seconds)


def client_replying(*texts: str | int) -> tuple[LLMClient, list[httpx.Request]]:
    """A client whose successive calls answer with `texts`; an int is an HTTP error status."""
    queue = list(texts)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, int):
            return httpx.Response(item)
        return httpx.Response(200, json={"choices": [{"message": {"content": item}}]})

    endpoint = Endpoint("primary", "https://a.test/v1", "m", SecretStr("k"), 5.0)
    return LLMClient([endpoint], transport=httpx.MockTransport(handler)), seen


async def test_the_screenshot_is_sent_as_an_image_url_with_the_prompt() -> None:
    client, seen = client_replying("[[500, 500]]")
    points = await find_points(client, b"\x89PNG-bytes", sleep=Sleeper())

    assert points == [(500.0, 500.0)]
    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert content[0]["type"] == "text" and "available seats" in content[0]["text"].lower()
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_an_empty_answer_is_retried_once_after_four_seconds() -> None:
    sleeper = Sleeper()
    client, seen = client_replying("no idea", "[[400, 600]]")
    assert await find_points(client, b"x", sleep=sleeper) == [(400.0, 600.0)]
    assert sleeper.pauses == [4.0] and len(seen) == 2


async def test_an_error_is_retried_once_after_two_seconds_then_gives_up() -> None:
    sleeper = Sleeper()
    client, seen = client_replying(500, 500)
    assert await find_points(client, b"x", sleep=sleeper) == []
    assert sleeper.pauses == [2.0] and len(seen) == 2


async def test_no_endpoint_configured_gives_no_points_without_waiting_forever() -> None:
    sleeper = Sleeper()
    assert await find_points(LLMClient([]), b"x", sleep=sleeper) == []
    assert sleeper.pauses == [2.0]
