"""Harkins parsing: pure helpers, and the extractors run on pages captured from harkins.com.

Browser tests load a saved fixture with `page.set_content()`, so they run exactly the code that
runs against the live site. If Harkins changes its markup, capture the new page with
`scripts/explore_site.py --strip --save-html ...` and these tests show what broke.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from playwright.async_api import Page, async_playwright

from showwatch.chains.harkins.scraper import (
    COMING_SOON_URL,
    NOW_SHOWING_URL,
    extract_movie_links,
    extract_showtimes,
    listings_from_links,
    merge_listings,
    parse_time,
)
from showwatch.domain.models import MovieListing

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harkins"
DAY = date(2026, 11, 17)


# --- pure helpers -------------------------------------------------------------------------------


def test_listings_use_the_url_slug_as_the_external_id() -> None:
    listings = listings_from_links(
        [("Ebenezer", "/movies/ebenezer"), ("Godzilla Minus Zero", "/movies/godzilla-minus-zero")],
        "now_showing",
    )
    assert listings == [
        MovieListing(
            "ebenezer", "Ebenezer", "https://www.harkins.com/movies/ebenezer", "now_showing"
        ),
        MovieListing(
            "godzilla-minus-zero",
            "Godzilla Minus Zero",
            "https://www.harkins.com/movies/godzilla-minus-zero",
            "now_showing",
        ),
    ]


def test_the_coming_soon_tab_and_empty_labels_are_not_movies() -> None:
    listings = listings_from_links(
        [("Coming Soon", "/movies/coming-soon"), ("", "/movies/x"), ("Real", "/movies/real?x=1")],
        "coming_soon",
    )
    assert [m.external_id for m in listings] == ["real"]


def test_a_movie_linked_twice_is_listed_once() -> None:
    listings = listings_from_links([("A", "/movies/a"), ("A again", "/movies/a/")], "now_showing")
    assert [(m.external_id, m.title) for m in listings] == [("a", "A")]


def test_a_title_on_both_pages_counts_as_now_showing() -> None:
    both_now = MovieListing("dune", "Dune", "u", "now_showing")
    both_soon = MovieListing("dune", "Dune", "u", "coming_soon")
    only_soon = MovieListing("moana", "Moana", "u", "coming_soon")
    merged = {m.external_id: m.status for m in merge_listings([both_now], [both_soon, only_soon])}
    assert merged == {"dune": "now_showing", "moana": "coming_soon"}


@pytest.mark.parametrize(
    ("text", "hour", "minute"),
    [
        ("4:20 PM", 16, 20),
        ("12:05 AM", 0, 5),
        ("12:30 PM", 12, 30),
        ("9:00 am", 9, 0),
        (" 7:05 PM ", 19, 5),
    ],
)
def test_showtimes_are_read_in_the_theatres_time_zone(text: str, hour: int, minute: int) -> None:
    start = parse_time(text, DAY)
    assert (start.hour, start.minute) == (hour, minute)
    assert start.utcoffset() is not None
    assert start.utcoffset().total_seconds() == -7 * 3600  # Arizona has no daylight saving time


def test_arizona_times_convert_to_utc_correctly() -> None:
    assert parse_time("1:15 PM", DAY).astimezone(UTC) == datetime(2026, 11, 17, 20, 15, tzinfo=UTC)


def test_the_site_urls_are_the_public_pages() -> None:
    assert NOW_SHOWING_URL == "https://www.harkins.com/movies"
    assert COMING_SOON_URL == "https://www.harkins.com/movies/coming-soon"


# --- extractors on real pages (need Chromium) ----------------------------------------------------


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


async def _load(page: Page, name: str) -> None:
    await page.set_content((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.browser
async def test_now_showing_page_yields_movies(page: Page) -> None:
    await _load(page, "now_showing.html")
    listings = listings_from_links(await extract_movie_links(page), "now_showing")
    by_id = {m.external_id: m for m in listings}
    assert len(listings) >= 40
    assert by_id["ebenezer"].title == "Ebenezer"
    assert by_id["ebenezer"].url == "https://www.harkins.com/movies/ebenezer"
    assert "coming-soon" not in by_id
    assert all(m.status == "now_showing" for m in listings)


@pytest.mark.browser
async def test_coming_soon_page_yields_movies(page: Page) -> None:
    await _load(page, "coming_soon.html")
    titles = {m.title for m in listings_from_links(await extract_movie_links(page), "coming_soon")}
    assert "Avengers: Doomsday" in titles
    assert len(titles) >= 40


@pytest.mark.browser
async def test_a_showtimes_page_yields_the_sessions_at_the_theatre(page: Page) -> None:
    await _load(page, "movie_showtimes.html")
    showtimes = await extract_showtimes(page, "16", DAY)

    assert [s.external_session_id for s in showtimes] == ["578869", "578870", "578871", "578872"]
    first = showtimes[0]
    assert first.theatre_external_id == "16"
    assert first.format == "Digital"  # read from the group heading
    assert first.starts_at.astimezone(UTC) == datetime(2026, 11, 17, 20, 15, tzinfo=UTC)
    assert first.booking_url == (
        "https://www.harkins.com/ticketing/theatre/16/movie/HO00015819/session/578869/date/2026-11-17"
    )
    assert [s.starts_at.hour for s in showtimes] == [13, 16, 19, 22]


@pytest.mark.browser
async def test_another_theatres_sessions_are_not_returned(page: Page) -> None:
    await _load(page, "movie_showtimes.html")
    assert await extract_showtimes(page, "99", DAY) == []


def _page_with_sessions(*anchors: str) -> str:
    return "<html><body>" + "".join(anchors) + "</body></html>"


def _link(session: str, text: str = "4:20 PM", css: str = "") -> str:
    href = f"/ticketing/theatre/16/movie/HO1/session/{session}/date/2026-11-17"
    return f'<a href="{href}" class="{css}">{text}</a>'


@pytest.mark.browser
async def test_started_sessions_are_skipped(page: Page) -> None:
    await page.set_content(_page_with_sessions(_link("1", css="x disabled"), _link("2")))
    assert [s.external_session_id for s in await extract_showtimes(page, "16", DAY)] == ["2"]


@pytest.mark.browser
async def test_a_link_without_a_session_id_is_skipped(page: Page) -> None:
    html = _page_with_sessions('<a href="/ticketing/theatre/16/about">info</a>', _link("7"))
    await page.set_content(html)
    assert [s.external_session_id for s in await extract_showtimes(page, "16", DAY)] == ["7"]


@pytest.mark.browser
async def test_sessions_outside_any_format_group_are_standard(page: Page) -> None:
    await page.set_content(_page_with_sessions(_link("3")))
    (showtime,) = await extract_showtimes(page, "16", DAY)
    assert showtime.format == "Standard"


@pytest.mark.browser
async def test_the_format_comes_from_the_group_testid_and_its_image_or_heading(page: Page) -> None:
    group = (
        '<div data-testid="movie-showtime-category">'
        '<h3>Heading</h3><img alt="IMAX"><ol><li>{a}</li></ol></div>'
        '<div data-testid="movie-showtime-category"><h3>CINÉ XL</h3><ol><li>{b}</li></ol></div>'
    ).format(a=_link("1"), b=_link("2"))
    await page.set_content(_page_with_sessions(group))
    formats = {s.external_session_id: s.format for s in await extract_showtimes(page, "16", DAY)}
    assert formats == {"1": "IMAX", "2": "CINÉ XL"}  # image text wins over the heading


@pytest.mark.browser
async def test_the_legacy_generated_class_still_finds_the_format(page: Page) -> None:
    """If Harkins drops the data-testid, the old class the legacy scraper used is the fallback."""
    group = f'<div class="sc-d9066650-9 other"><h3>3D</h3><ol><li>{_link("5")}</li></ol></div>'
    await page.set_content(_page_with_sessions(group))
    (showtime,) = await extract_showtimes(page, "16", DAY)
    assert showtime.format == "3D"


def test_a_show_after_midnight_starts_on_the_next_calendar_day() -> None:
    start = parse_time("12:05 AM", DAY)
    assert (start.day, start.hour, start.minute) == (18, 0, 5)  # Tuesday's page, Wednesday 00:05
    assert parse_time("3:59 AM", DAY).day == 18
    assert parse_time("4:00 AM", DAY).day == 17  # a morning show is the page's own day
    assert parse_time("11:59 PM", DAY).day == 17


def test_a_label_with_extra_text_still_yields_its_time() -> None:
    assert parse_time("7:05 PM  (Open Caption)", DAY).hour == 19


def test_a_label_with_no_time_is_an_error() -> None:
    with pytest.raises(ValueError, match="no time"):
        parse_time("Sold out", DAY)


@pytest.mark.browser
async def test_one_unreadable_label_does_not_lose_the_rest_of_the_page(page: Page) -> None:
    await page.set_content(_page_with_sessions(_link("1", "Sold out"), _link("2", "7:05 PM")))
    assert [s.external_session_id for s in await extract_showtimes(page, "16", DAY)] == ["2"]
