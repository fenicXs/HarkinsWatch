"""Inline-button builders and callback-data parsing.

Callback data is `<action>:<id>`: it carries record ids, never list positions, so an old
button can only ever act on the record it was made for. Each string is at most 64 bytes
(`Button` enforces it).
"""

import hashlib
from collections.abc import Sequence
from datetime import date

from showwatch.domain.models import City, Movie
from showwatch.notify import CALLBACK_DATA_MAX_BYTES, Button, Buttons

APPROVE = "approve"
DENY = "deny"
WATCHPICK = "watchpick"
UNWATCH = "unwatch"
CITY = "city"
WATCHFOR = "watchfor"
PICK = "pick"
DATE = "date"
FMT = "fmt"
BOOK = "book"

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


def watch_for(query: str) -> Button | None:
    """A "none of these" button that carries the typed title itself (there is no server-side
    state to hold it). None when the title is too long to fit in 64 bytes of callback data."""
    data = callback_data(WATCHFOR, query)
    if len(data.encode()) > CALLBACK_DATA_MAX_BYTES:
        return None
    return Button("None of these: keep watching for my words", data)


def book_seats(movie_id: int) -> Buttons:
    """The "Book seats" button on alerts: starts the date picker for this movie."""
    return [[Button("Book seats", callback_data(PICK, movie_id))]]


def date_choices(movie_id: int, days: Sequence[date]) -> Buttons:
    return [[Button(d.strftime("%a %b %d"), f"{DATE}:{movie_id}:{d.isoformat()}")] for d in days]


def format_code(fmt: str) -> str:
    """A short stable code for a screen type, so the button does not depend on list order."""
    return hashlib.sha1(fmt.encode()).hexdigest()[:8]  # noqa: S324 (an id, not security)


def format_choices(movie_id: int, day: date, formats: Sequence[str]) -> Buttons:
    """One button per screen type; its code identifies the format (the name may not fit 64 bytes)."""
    return [
        [Button(_label(fmt), f"{FMT}:{movie_id}:{day.isoformat()}:{format_code(fmt)}")]
        for fmt in formats
    ]


def time_choices(times: Sequence[tuple[int, str]]) -> Buttons:
    """One button per showtime: `(showtime_id, label)`."""
    return [[Button(label, callback_data(BOOK, showtime_id))] for showtime_id, label in times]


def watch_choices(items: Sequence[tuple[int, str]]) -> Buttons:
    """One button per watch: `(watch_item_id, label)`."""
    return [[Button(_label(label), callback_data(UNWATCH, item_id))] for item_id, label in items]


def city_choices(cities: Sequence[City]) -> Buttons:
    return [[Button(c.name, callback_data(CITY, c.code))] for c in cities]
