"""BookMyShow and District parsers, run on pages saved from the live sites (tests/fixtures)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from showwatch.chains.bms import scraper as bms
from showwatch.chains.browser import is_blocked
from showwatch.chains.district import scraper as district
from showwatch.domain.models import Movie

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _movie(url: str, external_id: str) -> Movie:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    return Movie(
        id=1,
        chain_id=1,
        city_id=1,
        external_id=external_id,
        title="x",
        normalized_title="x",
        url=url,
        status="coming_soon",
        first_seen_at=now,
        last_seen_at=now,
        released_at=None,
    )


# --- BookMyShow -------------------------------------------------------------------------------


def test_bms_movie_list_has_titles_ids_and_bengaluru_urls() -> None:
    listings = bms.parse_movie_list(_html("bms/movies.html"))
    assert len(listings) >= 15
    first = listings[0]
    assert (first.external_id, first.title) == ("ET00518333", "Ghost Soldier")
    assert first.url == "https://in.bookmyshow.com/movies/bengaluru/ghost-soldier/ET00518333"
    assert {m.status for m in listings} == {"coming_soon"}  # only a probe says tickets exist
    assert len({m.external_id for m in listings}) == len(listings)  # no duplicates


def test_bms_open_showtimes_page_lists_venues_and_times() -> None:
    seen = bms.parse_showtimes(_html("bms/showtimes_open.html"))
    assert seen.open
    assert seen.showtimes >= 10
    assert "INOX: Megaplex Mall of Asia Bangalore" in seen.venues


def test_bms_closed_showtimes_page_is_not_open() -> None:
    seen = bms.parse_showtimes(_html("bms/showtimes_closed.html"))
    assert (seen.open, seen.showtimes) == (False, 0)


def test_bms_showtimes_url_uses_the_slug_and_todays_date_in_india() -> None:
    movie = _movie("https://in.bookmyshow.com/movies/bengaluru/ramayana/ET00451914", "ET00451914")
    late_utc = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)  # already the 17th in India
    assert bms.showtimes_url(movie, late_utc) == (
        "https://in.bookmyshow.com/movies/bang/ramayana-bengaluru/buytickets/ET00451914/20261117"
    )


# --- District ---------------------------------------------------------------------------------


def test_district_movie_list_has_clean_titles_and_bengaluru_urls() -> None:
    listings = district.parse_movie_list(_html("district/movies.html"))
    by_id = {m.external_id: m for m in listings}
    assert by_id["MV176735"].title == "Avengers: Doomsday"
    assert by_id["MV176735"].url == (
        "https://www.district.in/movies/avengers-doomsday-movie-tickets-in-bengaluru-MV176735"
    )
    assert len(by_id) == len(listings)
    assert not any("|" in m.title for m in listings)  # a card's rating and language text is dropped


@pytest.mark.parametrize("page", ["open", "other", "advance"])
def test_district_pages_with_sessions_are_open(page: str) -> None:
    is_open, count = district.sessions_open(_html(f"district/showtimes_{page}.html"))
    assert is_open
    assert count > 0


def test_district_page_without_times_is_not_open() -> None:
    page = "<html><body><h1>Some Film</h1><p>Bookings open soon</p></body></html>"
    assert district.sessions_open(page) == (False, 0)


# --- the browser's idea of "stopped" ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "title", "blocked"),
    [
        (403, "anything", True),
        (429, "anything", True),
        (200, "Attention Required! | Cloudflare", True),
        (200, "Access Denied", True),
        (200, "Bengaluru Movie Tickets Online Booking", False),
        (None, "", False),
    ],
)
def test_is_blocked(status: int | None, title: str, blocked: bool) -> None:
    assert is_blocked(status, title) is blocked
