"""Seat-map vision: ask a vision model where the available seats are on the canvas screenshot.

Used when the DOM seat buttons cannot be trusted (large IMAX maps draw seats on a canvas and do
not mark taken seats as disabled). The model returns points; the booker clicks each one and keeps
only the points that really selected a seat.
"""

import asyncio
import base64
import json
import re
from collections.abc import Awaitable, Callable

import structlog

from showwatch.llm.client import Completion, LLMClient, LLMError

log = structlog.get_logger(__name__)

PROMPT = (
    "This is a cinema seat-selection map. Gold/amber icons (or red rocker icons) are AVAILABLE seats. "
    "Grey helmet icons, blue squares, and wheelchair icons are NOT selectable. "
    "Return up to 20 points, each on a DIFFERENT available seat, concentrated near the CENTER of the "
    "available seating and spread across a few adjacent columns and rows there. "
    "Return ONLY a JSON array of [y, x] points normalized 0-1000 relative to THIS image."
)

POINT_ARRAY = re.compile(r"\[\s*\[.*\]\s*\]", re.DOTALL)
ATTEMPTS = 2
PAUSE_AFTER_ERROR_S = 2.0
PAUSE_AFTER_EMPTY_S = 4.0

Point = tuple[float, float]  # (y, x), each 0-1000, origin top-left
Sleep = Callable[[float], Awaitable[None]]


def parse_points(text: str, *, order: str = "yx", scale: int = 1000) -> list[Point]:
    """Points from a model reply, converted to (y, x) on a 0-1000 scale. Bad entries are dropped."""
    match = POINT_ARRAY.search(text)
    if match is None:
        return []
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return []
    points: list[Point] = []
    for item in raw:
        if not isinstance(item, list) or len(item) < 2:
            continue
        first, second = item[0], item[1]
        if isinstance(first, bool) or isinstance(second, bool):
            continue
        if not isinstance(first, int | float) or not isinstance(second, int | float):
            continue
        y, x = (first, second) if order == "yx" else (second, first)
        y, x = y * 1000 / scale, x * 1000 / scale
        if 0 <= y <= 1000 and 0 <= x <= 1000:
            points.append((float(y), float(x)))
    return points


def _messages(png: bytes) -> list[dict[str, object]]:
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]


async def find_points(
    client: LLMClient, png: bytes, *, sleep: Sleep = asyncio.sleep
) -> list[Point]:
    """Available-seat points for a seat-map screenshot, or [] if the model never gave any."""
    for attempt in range(ATTEMPTS):
        pause = PAUSE_AFTER_EMPTY_S
        try:
            completion: Completion = await client.complete(_messages(png), max_tokens=800)
        except LLMError:
            pause = PAUSE_AFTER_ERROR_S
        else:
            endpoint = completion.endpoint
            points = parse_points(
                completion.text, order=endpoint.coord_order, scale=endpoint.coord_scale
            )
            if points:
                log.info("vision_points", endpoint=endpoint.name, count=len(points))
                return points
        if attempt + 1 < ATTEMPTS:
            await sleep(pause)
    return []
