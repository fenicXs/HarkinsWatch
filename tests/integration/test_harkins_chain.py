"""HarkinsChain's navigation (blocked-page detection, back-off, cleanup) with a scripted browser.

No real browser or network: Playwright is replaced by fakes that serve scripted pages, so
these tests cover the code the saved-page tests cannot (what happens when the site pushes back).
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.chains.base import BlockedError
from showwatch.chains.harkins import scraper
from showwatch.chains.harkins.scraper import HarkinsChain
from showwatch.chains.politeness import Politeness
from showwatch.db.repositories import catalog
from showwatch.domain.models import City, Movie, ShowtimeListing, Theatre

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class FakePage:
    def __init__(
        self,
        *,
        status: int = 200,
        title: str = "Harkins Theatres | Harkins Movies",
        links: list[tuple[str, str]] | None = None,
        content_appears: bool = True,
    ) -> None:
        self.status = status
        self._title = title
        self.links = links or []
        self.content_appears = content_appears
        self.url = ""

    async def goto(self, url: str, wait_until: str) -> Any:
        self.url = url
        return SimpleNamespace(status=self.status)

    async def title(self) -> str:
        return self._title

    async def wait_for_selector(self, selector: str, timeout: int) -> None:
        if not self.content_appears:
            raise TimeoutError("selector never appeared")


class FakeBrowser:
    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = list(pages)
        self.requested: list[FakePage] = []
        self.closed = False

    async def new_page(self) -> FakePage:
        page = self._pages.pop(0)
        self.requested.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = SimpleNamespace(launch=self._launch)
        self._browser = browser

    async def _launch(self, headless: bool) -> FakeBrowser:
        return self._browser

    async def __aenter__(self) -> "FakePlaywright":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def serve(monkeypatch: pytest.MonkeyPatch, *pages: FakePage) -> FakeBrowser:
    """Make the scraper's browser serve these pages, in order."""
    browser = FakeBrowser(list(pages))
    monkeypatch.setattr(scraper, "async_playwright", lambda: FakePlaywright(browser))

    async def links_of(page: FakePage) -> list[tuple[str, str]]:
        return page.links

    monkeypatch.setattr(scraper, "extract_movie_links", links_of)
    return browser


async def _setup(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[HarkinsChain, Clock, City, Theatre]:
    clock = Clock()
    chain = HarkinsChain(Politeness(session_factory, per_minute=6000, now=clock))
    async with session_factory() as session:
        phx = await catalog.get_city_by_code(session, "phx")
        harkins = await catalog.get_chain_by_code(session, "harkins")
        assert phx and harkins
        theatre = (await catalog.list_theatres(session, harkins.id, phx.id))[0]
    return chain, clock, phx, theatre


async def _blocked_until(factory: async_sessionmaker[AsyncSession]) -> datetime | None:
    async with factory() as session:
        info = await catalog.get_chain_by_code(session, "harkins")
    assert info
    return info.blocked_until


NOW = [("Moana", "/movies/moana"), ("Ebenezer", "/movies/ebenezer")]
SOON = [("Dune", "/movies/dune"), ("Moana", "/movies/moana")]


async def test_both_pages_are_read_merged_and_the_browser_is_closed(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, _, phx, _ = await _setup(session_factory)
    browser = serve(monkeypatch, FakePage(links=NOW), FakePage(links=SOON))

    listings = await chain.list_movies(phx)

    assert {(m.external_id, m.status) for m in listings} == {
        ("moana", "now_showing"),  # on both pages: now showing wins
        ("ebenezer", "now_showing"),
        ("dune", "coming_soon"),
    }
    assert [p.url for p in browser.requested] == [scraper.NOW_SHOWING_URL, scraper.COMING_SOON_URL]
    assert browser.closed
    assert await _blocked_until(session_factory) is None


async def test_a_city_without_a_harkins_theatre_is_not_scraped(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, _, _, _ = await _setup(session_factory)
    browser = serve(monkeypatch)
    async with session_factory() as session:
        blr = await catalog.get_city_by_code(session, "blr")
    assert blr
    assert await chain.list_movies(blr) == []
    assert browser.requested == []


@pytest.mark.parametrize("status", [403, 429])
async def test_a_refusal_starts_a_back_off_and_stops_the_scrape(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    chain, clock, phx, _ = await _setup(session_factory)
    browser = serve(monkeypatch, FakePage(status=status), FakePage(links=SOON))

    with pytest.raises(BlockedError):
        await chain.list_movies(phx)

    assert len(browser.requested) == 1  # the second page was never requested
    assert browser.closed
    assert await _blocked_until(session_factory) == clock.now + timedelta(minutes=5)


@pytest.mark.parametrize(
    "title", ["Access Denied", "Just a moment...", "Attention Required! | Cloudflare"]
)
async def test_a_challenge_page_counts_as_a_block_even_with_status_200(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    chain, clock, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(title=title))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    assert await _blocked_until(session_factory) == clock.now + timedelta(minutes=5)


async def test_while_blocked_no_page_is_requested(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, clock, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)

    clock.now += timedelta(minutes=2)  # still inside the 5-minute block
    browser = serve(monkeypatch, FakePage(links=NOW), FakePage(links=SOON))
    with pytest.raises(BlockedError, match="backing off"):
        await chain.list_movies(phx)
    assert browser.requested == []  # the site was left alone


async def test_the_back_off_grows_even_when_the_first_page_loads_fine(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a clean first page must not reset the back-off before the second page is read."""
    chain, clock, phx, _ = await _setup(session_factory)
    waits = []
    for _ in range(3):
        serve(monkeypatch, FakePage(links=NOW), FakePage(status=403))  # page 1 ok, page 2 refused
        with pytest.raises(BlockedError):
            await chain.list_movies(phx)
        until = await _blocked_until(session_factory)
        assert until is not None
        waits.append((until - clock.now) / timedelta(minutes=1))
        clock.now = until + timedelta(seconds=1)  # wait the block out
    assert waits == [5, 10, 20]


async def test_a_clean_scrape_forgets_the_back_off(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, clock, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    clock.now += timedelta(minutes=6)

    serve(monkeypatch, FakePage(links=NOW), FakePage(links=SOON))
    await chain.list_movies(phx)  # a fully clean scrape
    assert await _blocked_until(session_factory) is None

    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    assert await _blocked_until(session_factory) == clock.now + timedelta(
        minutes=5
    )  # back to step one


async def test_a_page_whose_content_never_appears_gives_no_movies_not_an_error(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, _, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(content_appears=False), FakePage(content_appears=False))
    assert await chain.list_movies(phx) == []


async def test_showtimes_are_read_from_the_movies_dated_page(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, _, phx, theatre = await _setup(session_factory)
    browser = serve(monkeypatch, FakePage())
    expected = [ShowtimeListing("1", "16", T0, "Digital", "https://www.harkins.com/x")]
    seen: list[tuple[Any, str, date]] = []

    async def fake_extract(page: Any, theatre_id: str, day: date) -> list[ShowtimeListing]:
        seen.append((page, theatre_id, day))
        return expected

    monkeypatch.setattr(scraper, "extract_showtimes", fake_extract)
    movie = Movie(
        id=1,
        chain_id=1,
        city_id=phx.id,
        external_id="moana",
        title="Moana",
        normalized_title="moana",
        url="https://www.harkins.com/movies/moana/",
        status="now_showing",
        first_seen_at=T0,
        last_seen_at=T0,
        released_at=None,
    )

    result = await chain.list_showtimes(movie, theatre, date(2026, 11, 17))

    assert result == expected
    assert browser.requested[0].url == "https://www.harkins.com/movies/moana/2026-11-17"
    assert [(t, d) for _, t, d in seen] == [("16", date(2026, 11, 17))]
    assert browser.closed


async def test_the_chain_declares_what_it_can_do() -> None:
    from showwatch.domain.models import Capability

    assert HarkinsChain.capabilities == Capability.WATCH | Capability.SHOWTIMES
    assert HarkinsChain.code == "harkins"


async def test_a_server_error_fails_the_scrape_without_calling_it_clean_or_blocked(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    chain, clock, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    blocked_until = await _blocked_until(session_factory)
    clock.now = blocked_until + timedelta(seconds=1)

    serve(monkeypatch, FakePage(status=500))
    with pytest.raises(RuntimeError, match="HTTP 500"):  # a plain failure, retried by the job
        await chain.list_movies(phx)
    assert await _blocked_until(session_factory) == blocked_until  # neither cleared nor extended


async def test_an_empty_result_does_not_reset_the_back_off(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognised block page looks like 'no movies'; it must not count as a clean scrape."""
    chain, clock, phx, _ = await _setup(session_factory)
    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    clock.now += timedelta(minutes=6)

    serve(monkeypatch, FakePage(content_appears=False), FakePage(content_appears=False))
    assert await chain.list_movies(phx) == []  # empty, so not "clean"

    serve(monkeypatch, FakePage(status=403))
    with pytest.raises(BlockedError):
        await chain.list_movies(phx)
    until = await _blocked_until(session_factory)
    assert until == clock.now + timedelta(minutes=10)  # still escalating, not back to 5
