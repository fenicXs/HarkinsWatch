"""BookMyShow (Bengaluru), watch-only: the movie list and "have tickets opened for this movie?".

Pages are read from a normal browser window without login; the site's own JSON calls are blocked
(`docs/decisions/019-bms-probe.md`), so nothing here calls them. A movie's showtimes page lists
venues and times once sessions exist and says "We couldn't find anything" before that.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from showwatch.chains.base import BaseChain
from showwatch.chains.browser import PageFetcher
from showwatch.chains.pages import clock_times, read_page
from showwatch.chains.politeness import Politeness
from showwatch.domain.models import Capability, City, Movie, MovieListing, Signal

BASE_URL = "https://in.bookmyshow.com"
LIST_URL = f"{BASE_URL}/explore/movies-bengaluru"
SITE_TZ = ZoneInfo("Asia/Kolkata")

_MOVIE_HREF = re.compile(r"/movies/(?:[a-z]+/)?([a-z0-9-]+)/(ET\d+)", re.IGNORECASE)
_VENUE_HREF = re.compile(r"/cinemas/[^/]+/[^/]+/buytickets/", re.IGNORECASE)
CLOSED_MESSAGE = "couldn't find anything"

LIST_SCROLL_PASSES = 6  # the list loads as you scroll
LIST_READY_JS = "document.querySelectorAll(\"a[href*='/movies/'][href*='/ET']\").length > 5"
SHOWTIMES_READY_JS = (
    "/\\d{1,2}:\\d{2}\\s?[AP]M|couldn.t find anything/i.test(document.body.innerText)"
)


@dataclass(frozen=True)
class SessionsSeen:
    open: bool
    venues: list[str]
    showtimes: int


def parse_movie_list(html: str) -> list[MovieListing]:
    """Every movie on the Bengaluru page. All are `coming_soon`: only a probe says tickets exist."""
    anchors, _ = read_page(html)
    listings: dict[str, MovieListing] = {}
    for anchor in anchors:
        found = _MOVIE_HREF.search(anchor.href)
        if found is None or "/buytickets/" in anchor.href:
            continue
        title = next((label for label in anchor.labels if label), "") or anchor.text
        if not title:
            continue
        external_id = found.group(2).upper()
        url = f"{BASE_URL}/movies/bengaluru/{found.group(1)}/{external_id}"
        listings.setdefault(
            external_id, MovieListing(external_id, title.strip(), url, "coming_soon")
        )
    return list(listings.values())


def parse_showtimes(html: str) -> SessionsSeen:
    """Whether the showtimes page lists any sessions, and at which venues."""
    anchors, texts = read_page(html)
    count = clock_times(texts)
    closed = any(CLOSED_MESSAGE in text.lower() for text in texts)
    venues = [
        a.labels[0]
        for a in anchors
        if _VENUE_HREF.search(a.href) and a.labels and a.labels[0].strip()
    ]
    return SessionsSeen(
        open=count > 0 and not closed, venues=list(dict.fromkeys(venues)), showtimes=count
    )


def showtimes_url(movie: Movie, now: datetime | None = None) -> str:
    """The movie's showtimes page for today (the site shows the next 7 days from there)."""
    found = _MOVIE_HREF.search(movie.url)
    slug = found.group(1) if found else movie.external_id.lower()
    day = (now or datetime.now(SITE_TZ)).astimezone(SITE_TZ).strftime("%Y%m%d")
    return f"{BASE_URL}/movies/bang/{slug}-bengaluru/buytickets/{movie.external_id}/{day}"


class BookMyShowChain(BaseChain):
    code = "bms"
    capabilities = Capability.WATCH | Capability.SIGNALS

    def __init__(
        self, politeness: Politeness, *, channel: str | None = None, headless: bool = False
    ) -> None:
        self._politeness = politeness
        self._channel = channel
        self._headless = headless

    def _fetcher(self) -> PageFetcher:
        return PageFetcher(
            self.code, self._politeness, channel=self._channel, headless=self._headless
        )

    async def list_movies(self, city: City) -> list[MovieListing]:
        if city.code != "blr":
            return []
        async with self._fetcher() as fetcher:
            return parse_movie_list(
                await fetcher.get(
                    LIST_URL, ready_js=LIST_READY_JS, scroll_passes=LIST_SCROLL_PASSES
                )
            )

    async def probe_signals(self, movie: Movie) -> list[Signal]:
        url = showtimes_url(movie)
        async with self._fetcher() as fetcher:
            html = await fetcher.get(url, ready_js=SHOWTIMES_READY_JS)
        seen = parse_showtimes(html)
        if not seen.open:
            return []
        evidence: dict[str, object] = {
            "venues": seen.venues[:5],
            "showtimes": seen.showtimes,
            "url": url,
        }
        return [Signal("sessions", evidence)]
