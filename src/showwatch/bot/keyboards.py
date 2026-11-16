"""Inline-button builders and callback-data parsing.

Callback data is `<action>:<id>`: it carries record ids, never list positions, so an old
button can only ever act on the record it was made for. Each string is at most 64 bytes
(`Button` enforces it).
"""

from collections.abc import Sequence

from showwatch.domain.models import City, Movie
from showwatch.notify import Button, Buttons

APPROVE = "approve"
DENY = "deny"
WATCHPICK = "watchpick"
UNWATCH = "unwatch"
CITY = "city"

MAX_LABEL = 60  # keep long movie titles from filling the screen


def callback_data(action: str, ref: int | str) -> str:
    return f"{action}:{ref}"


def parse_callback(data: str) -> tuple[str, str] | None:
    """Split `<action>:<ref>`; None if it is not in that shape."""
    action, sep, ref = data.partition(":")
    if not sep or not action or not ref:
        return None
    return action, ref


def _label(text: str) -> str:
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


def approve_deny(user_id: int) -> Buttons:
    return [
        [
            Button("Approve", callback_data(APPROVE, user_id)),
            Button("Deny", callback_data(DENY, user_id)),
        ]
    ]


def movie_choices(movies: Sequence[Movie]) -> Buttons:
    return [[Button(_label(m.title), callback_data(WATCHPICK, m.id))] for m in movies]


def watch_choices(items: Sequence[tuple[int, str]]) -> Buttons:
    """One button per watch: `(watch_item_id, label)`."""
    return [[Button(_label(label), callback_data(UNWATCH, item_id))] for item_id, label in items]


def city_choices(cities: Sequence[City]) -> Buttons:
    return [[Button(c.name, callback_data(CITY, c.code))] for c in cities]
