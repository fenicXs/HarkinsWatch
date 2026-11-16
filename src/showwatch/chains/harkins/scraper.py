"""Harkins Theatres: the movie lists and a movie's showtimes, scraped from public pages.

The `extract_*` functions read an already-loaded Playwright page, so tests can load a saved
fixture with `page.set_content()` and run exactly the code that runs against the live site.
Rules carried over from the legacy scraper (`legacy/scraper.py`): links without an
`aria-label` are skipped; showtime links whose class contains "disabled" (already started) are
skipped; the format defaults to "Standard".
"""

import re
from datetime import date, datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import structlog
from playwright.async_api import Browser, Page, async_playwright

from showwatch.chains.base import BaseChain, BlockedError
from showwatch.chains.politeness import Politeness
from showwatch.domain.models import (
    Capability,
    City,
    Movie,
    MovieListing,
    MovieStatus,
    ShowtimeListing,
    Theatre,
)

BASE_URL = "https://www.harkins.com"
NOW_SHOWING_URL = f"{BASE_URL}/movies"
COMING_SOON_URL = f"{BASE_URL}/movies/coming-soon"
SITE_TZ = ZoneInfo("America/Phoenix")  # Arizona Mills; the only Harkins theatre seeded so far

MOVIE_LINK_SELECTOR = "a[href^='/movies/']"
SHOWTIME_LINK_SELECTOR = "a[href*='/ticketing/theatre/']"
PAGE_READY_TIMEOUT_MS = 15_000
SHOWTIMES_READY_TIMEOUT_MS = 8_000  # a day with no showtimes has nothing to wait for

# Pages that mean "we were stopped", not "no data".
CHALLENGE_TITLE = re.compile(
    r"access denied|just a moment|attention required|pardon our interruption|request unsuccessful",
    re.IGNORECASE,
)
BLOCKING_STATUSES = frozenset({403, 429})

# The format label ("Digital", "IMAX", "CINÉ XL"...) of the group a showtime link sits in. The
# group has a stable data-testid; the generated class below is the legacy fallback.
FORMAT_JS = """el => {
  const label = group => {
    const img = group.querySelector('img[alt]');
    if (img && img.alt) return img.alt.trim();
    const h = group.querySelector('h3,h4');
    if (h && h.textContent) return h.textContent.trim();
    return (group.getAttribute('data-testid') || '').trim();
  };
  const group = el.closest('[data-testid="movie-showtime-category"]');
  if (group) return label(group);
  let node = el;
  while (node) {
    if (node.className && String(node.className).includes('sc-d9066650-9')) return label(node);
    node = node.parentElement;
  }
  return '';
}"""

_SESSION_ID = re.compile(r"/session/(\d+)")
_THEATRE_ID = re.compile(r"/ticketing/theatre/(\d+)/")

log = structlog.get_logger(__name__)


def listings_from_links(links: list[tuple[str, str]], status: MovieStatus) -> list[MovieListing]:
    """Movie listings from (title, href) pairs. The slug in the href is the external id."""
    listings: dict[str, MovieListing] = {}
    for title, href in links:
        slug = href.removeprefix("/movies/").strip("/").split("/")[0].split("?")[0]
        if not title or not slug or slug == "coming-soon":
            continue  # the "Coming Soon" tab is a link too
        listings.setdefault(slug, MovieListing(slug, title, urljoin(BASE_URL, href), status))
    return list(listings.values())


def merge_listings(
    now_showing: list[MovieListing], coming_soon: list[MovieListing]
) -> list[MovieListing]:
    """Both pages together. A title on both pages counts as now showing."""
    merged = {m.external_id: m for m in coming_soon}
    merged.update({m.external_id: m for m in now_showing})
    return list(merged.values())


def parse_time(text: str, day: date) -> datetime:
    """'4:20 PM' on `day`, in the theatre's time zone, as an aware datetime."""
    clock = datetime.strptime(text.strip().upper(), "%I:%M %p")
    return datetime.combine(day, clock.time(), tzinfo=SITE_TZ)


async def extract_movie_links(page: Page) -> list[tuple[str, str]]:
    """(title, href) for every movie link that has an aria-label."""
    links: list[tuple[str, str]] = []
    anchors = page.locator(MOVIE_LINK_SELECTOR)
    for index in range(await anchors.count()):
        anchor = anchors.nth(index)
        title = await anchor.get_attribute("aria-label")
        href = await anchor.get_attribute("href")
        if title and href:
            links.append((title, href))
    return links


async def extract_showtimes(
    page: Page, theatre_external_id: str, day: date
) -> list[ShowtimeListing]:
    """The bookable sessions at one theatre on one day."""
    prefix = f"/ticketing/theatre/{theatre_external_id}/"
    showtimes: list[ShowtimeListing] = []
    anchors = page.locator(f"a[href*='{prefix}']")
    for index in range(await anchors.count()):
        anchor = anchors.nth(index)
        if "disabled" in (await anchor.get_attribute("class") or ""):
            continue  # already started
        href = await anchor.get_attribute("href") or ""
        session = _SESSION_ID.search(href)
        if session is None:
            continue
        label = (await anchor.inner_text()).strip()
        fmt = (await anchor.evaluate(FORMAT_JS)) or "Standard"
        showtimes.append(
            ShowtimeListing(
                external_session_id=session.group(1),
                theatre_external_id=theatre_external_id,
                starts_at=parse_time(label, day),
                format=fmt,
                booking_url=urljoin(BASE_URL, href),
            )
        )
    return showtimes


class HarkinsChain(BaseChain):
    code = "harkins"
    capabilities = Capability.WATCH | Capability.SHOWTIMES  # BOOK arrives with M4

    def __init__(self, politeness: Politeness) -> None:
        self._politeness = politeness

    async def list_movies(self, city: City) -> list[MovieListing]:
        if city.code != "phx":
            return []  # the only Harkins theatre seeded is in Phoenix
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                now_showing = await self._movies_on(browser, NOW_SHOWING_URL, "now_showing")
                coming_soon = await self._movies_on(browser, COMING_SOON_URL, "coming_soon")
            finally:
                await browser.close()
        return merge_listings(now_showing, coming_soon)

    async def list_showtimes(
        self, movie: Movie, theatre: Theatre, day: date
    ) -> list[ShowtimeListing]:
        url = f"{movie.url.rstrip('/')}/{day.isoformat()}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await self._load(
                    browser, url, SHOWTIME_LINK_SELECTOR, SHOWTIMES_READY_TIMEOUT_MS
                )
                return await extract_showtimes(page, theatre.external_id, day)
            finally:
                await browser.close()

    async def _movies_on(
        self, browser: Browser, url: str, status: MovieStatus
    ) -> list[MovieListing]:
        page = await self._load(
            browser, url, f"{MOVIE_LINK_SELECTOR}[aria-label]", PAGE_READY_TIMEOUT_MS
        )
        return listings_from_links(await extract_movie_links(page), status)

    async def _load(self, browser: Browser, url: str, ready_selector: str, timeout_ms: int) -> Page:
        """Open a page politely: budget first, then recognise a block when the site pushes back."""
        await self._politeness.before_request(self.code)
        page = await browser.new_page()
        response = await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        status = response.status if response else None
        if status in BLOCKING_STATUSES or CHALLENGE_TITLE.search(title):
            until = await self._politeness.record_blocked(self.code)
            raise BlockedError(
                f"harkins.com stopped us (status {status}); backing off until {until}"
            )
        try:
            await page.wait_for_selector(ready_selector, timeout=timeout_ms)
        except Exception:
            log.info("page_has_no_matching_content", url=url, selector=ready_selector)
        await self._politeness.record_ok(self.code)
        return page
