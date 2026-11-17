"""BookMyShow (Bengaluru), watch-only: the movie list and "have tickets opened for this movie?".

Pages are read from a normal browser window without login; the site's own JSON calls are blocked
(`docs/decisions/019-bms-probe.md`), so nothing here calls them. A movie's showtimes page lists
venues and times once sessions exist and says "We couldn't find anything" before that.

A movie has one showtimes page per screen format (2D, a 3D version, "MS - Infinity Vision"...),
and the page's own state lists them all in a format selector. `probe_signals` reads the
selector and looks at each format page the person could want, so a 2D opening is told apart from
a 3D one.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from showwatch.chains.base import BaseChain
from showwatch.chains.browser import PageFetcher
from showwatch.chains.pages import clock_times, read_page
from showwatch.chains.politeness import Politeness
from showwatch.domain.formats import FormatPref, classify
from showwatch.domain.models import Capability, City, Movie, MovieListing, Signal

BASE_URL = "https://in.bookmyshow.com"
LIST_URL = f"{BASE_URL}/explore/movies-bengaluru"
SITE_TZ = ZoneInfo("Asia/Kolkata")
MAX_FORMAT_PAGES = 4  # other format pages looked at per probe: keeps a probe polite

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


@dataclass(frozen=True)
class FormatEntry:
    """One screen format of a movie: its label, language and the page that lists its shows."""

    label: str
    language: str
    code: str  # the ET id of that format's page
    slug: str


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


def _state(html: str) -> dict[str, Any]:
    """The page's `window.__INITIAL_STATE__` object, or {} if it is missing or unreadable."""
    start = html.find("window.__INITIAL_STATE__")
    brace = html.find("{", start) if start >= 0 else -1
    if brace < 0:
        return {}
    try:
        state, _ = json.JSONDecoder().raw_decode(html[brace:])
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def _dynamic_data(html: str) -> dict[str, Any]:
    queries = _state(html).get("showtimesFunctionalApi", {}).get("queries", {})
    for name, query in queries.items():
        if name.startswith("fetchPrimaryDynamic") and isinstance(query, dict):
            data = query.get("data")
            return data if isinstance(data, dict) else {}
    return {}


def parse_format_entries(html: str) -> list[FormatEntry]:
    """The screen formats the page's selector offers (2D, a 3D version...), each with its page."""
    selector = (
        _dynamic_data(html).get("data", {}).get("bottomSheetData", {}).get("format-selector", {})
    )
    entries: dict[str, FormatEntry] = {}
    for widget in selector.get("widgets", []):
        if not isinstance(widget, dict) or widget.get("type") != "chip-list":
            continue
        for item in widget.get("data", []):
            extra = (item.get("cta") or {}).get("additionalData") or {}
            code, slug, title = extra.get("eventCode"), extra.get("eventUrl"), item.get("title")
            if code and code != "*" and slug and title:
                entries.setdefault(
                    code, FormatEntry(str(title), str(widget.get("text") or ""), code, str(slug))
                )
    return list(entries.values())


def page_format(html: str) -> str:
    """The format label of the page itself, when the page says (it does for a single format)."""
    analytics = _dynamic_data(html).get("metadata", {}).get("analytics", {})
    return str(analytics.get("format") or "")


def showtimes_url(movie: Movie, now: datetime | None = None) -> str:
    """The movie's showtimes page for today (the site shows the next 7 days from there)."""
    found = _MOVIE_HREF.search(movie.url)
    slug = found.group(1) if found else movie.external_id.lower()
    return _showtimes_url(slug, movie.external_id, now)


def _showtimes_url(slug: str, code: str, now: datetime | None = None) -> str:
    day = (now or datetime.now(SITE_TZ)).astimezone(SITE_TZ).strftime("%Y%m%d")
    return f"{BASE_URL}/movies/bang/{slug}-bengaluru/buytickets/{code}/{day}"


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

    async def probe_signals(self, movie: Movie, pref: FormatPref | None = None) -> list[Signal]:
        """A `sessions` signal listing every format that has shows, or [] if none has."""
        pref = pref or FormatPref()
        url = showtimes_url(movie)
        opened: list[dict[str, object]] = []
        async with self._fetcher() as fetcher:
            html = await fetcher.get(url, ready_js=SHOWTIMES_READY_JS)
            entries = parse_format_entries(html)
            own = next((e for e in entries if e.code == movie.external_id), None)
            label = own.label if own else page_format(html)
            seen = parse_showtimes(html)
            if seen.open:
                opened.append(_found(label, seen, url))
            others = [
                e
                for e in entries
                if e.code != movie.external_id and classify(e.label, pref) != "no"
            ]
            for entry in others[:MAX_FORMAT_PAGES]:
                format_url = _showtimes_url(entry.slug, entry.code)
                format_seen = parse_showtimes(
                    await fetcher.get(format_url, ready_js=SHOWTIMES_READY_JS)
                )
                if format_seen.open:
                    opened.append(_found(entry.label, format_seen, format_url))
        if not opened:
            return []
        evidence: dict[str, object] = {"formats": opened, "url": opened[0]["url"]}
        return [Signal("sessions", evidence)]


def _found(label: str, seen: SessionsSeen, url: str) -> dict[str, object]:
    return {"label": label, "showtimes": seen.showtimes, "venues": seen.venues[:5], "url": url}
