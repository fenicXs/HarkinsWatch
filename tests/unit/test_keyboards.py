from datetime import UTC, date, datetime

import pytest

from showwatch.bot import keyboards
from showwatch.domain.models import City, Movie
from showwatch.notify import CALLBACK_DATA_MAX_BYTES, Button


def _movie(movie_id: int, title: str) -> Movie:
    return Movie(
        id=movie_id,
        chain_id=1,
        city_id=1,
        external_id="x",
        title=title,
        normalized_title="x",
        url="u",
        status="coming_soon",
        first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        released_at=None,
    )


def test_approve_and_deny_carry_the_user_id() -> None:
    (row,) = keyboards.approve_deny(42)
    assert [(b.text, b.data) for b in row] == [("Approve", "approve:42"), ("Deny", "deny:42")]


def test_movie_buttons_carry_the_movie_id_not_a_position() -> None:
    rows = keyboards.movie_choices([_movie(17, "Dune"), _movie(9, "Moana")])
    assert [row[0].data for row in rows] == ["watchpick:17", "watchpick:9"]


def test_long_titles_are_shortened_for_the_label_only() -> None:
    (row,) = keyboards.movie_choices([_movie(1, "x" * 200)])
    assert len(row[0].text) == keyboards.MAX_LABEL
    assert row[0].text.endswith("…")
    assert row[0].data == "watchpick:1"


def test_watch_and_city_buttons() -> None:
    assert [r[0].data for r in keyboards.watch_choices([(5, "Dune"), (6, "Moana")])] == [
        "unwatch:5",
        "unwatch:6",
    ]
    cities = [City(id=1, code="phx", name="Phoenix", tz="America/Phoenix")]
    assert [(r[0].text, r[0].data) for r in keyboards.city_choices(cities)] == [
        ("Phoenix", "city:phx")
    ]


@pytest.mark.parametrize(
    ("data", "expected"),
    [("approve:42", ("approve", "42")), ("city:phx", ("city", "phx")), ("a:b:c", ("a", "b:c"))],
)
def test_parse_callback(data: str, expected: tuple[str, str]) -> None:
    assert keyboards.parse_callback(data) == expected


@pytest.mark.parametrize("data", ["", "nocolon", ":5", "approve:"])
def test_parse_callback_rejects_malformed_data(data: str) -> None:
    assert keyboards.parse_callback(data) is None


def test_a_button_over_64_bytes_is_refused() -> None:
    assert Button("ok", "x" * CALLBACK_DATA_MAX_BYTES)
    with pytest.raises(ValueError, match="64 bytes"):
        Button("too long", "x" * (CALLBACK_DATA_MAX_BYTES + 1))


def test_the_longest_real_callback_is_far_below_the_limit() -> None:
    biggest = keyboards.callback_data(keyboards.WATCHPICK, 2**63 - 1)  # a bigint id
    assert len(biggest.encode()) <= CALLBACK_DATA_MAX_BYTES


def test_none_of_these_button_carries_the_typed_words() -> None:
    button = keyboards.watch_for("dune messiah")
    assert button is not None
    assert button.data == "watchfor:dune messiah"


def test_none_of_these_button_is_left_out_when_the_words_do_not_fit() -> None:
    assert keyboards.watch_for("x" * 60) is None  # "watchfor:" + 60 is over 64 bytes
    assert keyboards.watch_for("é" * 30) is None  # 60 bytes of text plus the prefix


def test_a_format_code_is_short_stable_and_different_per_format() -> None:
    assert keyboards.format_code("IMAX") == keyboards.format_code("IMAX")
    assert keyboards.format_code("IMAX") != keyboards.format_code("Digital")
    assert len(keyboards.format_code("CINÉ XL")) == 8


def test_format_buttons_use_the_code_so_a_long_name_still_fits() -> None:
    long_name = "Dolby Cinema at Harkins with Reclining Seats " * 3
    (row,) = keyboards.format_choices(7, date(2026, 11, 17), [long_name])
    assert row[0].data == f"fmt:7:2026-11-17:{keyboards.format_code(long_name)}"
    assert len(row[0].data.encode()) <= CALLBACK_DATA_MAX_BYTES
