"""District by Zomato (Bengaluru), watch-only: the movie list and "have sessions opened?".

District serves its pages to a normal browser window without login and listed sessions for a
December release weeks ahead (`docs/decisions/020-district-probe.md`), so it is often the earlier
signal. The Bengaluru page for a movie shows venues with times once sessions exist, and each
session also carries a structured record (schema.org `ScreeningEvent`) with its screen format,
so a 2D opening can be told apart from a 3D one.
"""

import json
import re
from collections import Counter

from showwatch.chains.base import BaseChain
from showwatch.chains.browser import PageFetcher
from showwatch.chains.pages import clock_times, read_page
from showwatch.chains.politeness import Politeness
from showwatch.domain.formats import FormatPref
from showwatch.domain.models import Capability, City, Movie, MovieListing, Signal

BASE_URL = "https://www.district.in"
LIST_URL = f"{BASE_URL}/movies/"

_MOVIE_HREF = re.compile(
    r"/movies/([a-z0-9-]+?)-movie-tickets(?:-in-[a-z-]+?)?-(MV\d+)$", re.IGNORECASE
)
_LD_JSON = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_NAME_FORMAT = re.compile(r"\(([^()]+)\)\s*$")

LIST_SCROLL_PASSES = 6  # the list loads as you scroll
LIST_READY_JS = "document.querySelectorAll(\"a[href*='movie-tickets']\").length > 5"
SHOWTIMES_READY_JS = "/\\d{1,2}:\\d{2}\\s?[AP]M/i.test(document.body.innerText)"


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


def parse_movie_list(html: str) -> list[MovieListing]:
    """Every movie on District's page, as its Bengaluru page. All are `coming_soon`."""
    anchors, _ = read_page(html)
    best: dict[str, tuple[bool, MovieListing]] = {}
    for anchor in anchors:
        found = _MOVIE_HREF.search(anchor.href.split("?")[0].split("#")[0].rstrip("/"))
        if found is None:
            continue
        slug, external_id = found.group(1), found.group(2).upper()
        title = next((label for label in anchor.labels if label), "")
        if not title and "|" not in anchor.text and len(anchor.text) <= 60:
            title = anchor.text  # a plain text link; a card's text mixes in rating and language
        real_title = bool(title)
        listing = MovieListing(
            external_id,
            (title or _title_from_slug(slug)).strip(),
            f"{BASE_URL}/movies/{slug}-movie-tickets-in-bengaluru-{external_id}",
            "coming_soon",
        )
        if external_id not in best or (real_title and not best[external_id][0]):
            best[external_id] = (real_title, listing)
    return [listing for _, listing in best.values()]


def extract_screenings(html: str) -> Counter[str]:
    """How many sessions each screen format has on the page (from its structured records)."""
    counts: Counter[str] = Counter()
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or item.get("@type") != "ScreeningEvent":
                continue
            label = str(item.get("videoFormat") or "").strip()
            if not label:
                named = _NAME_FORMAT.search(str(item.get("name") or ""))
                label = named.group(1).strip() if named else ""
            counts[label] += 1
    return counts


def sessions_open(html: str) -> tuple[bool, int]:
    """(are there sessions, how many show times) on a movie's Bengaluru page."""
    _, texts = read_page(html)
    count = clock_times(texts)
    return count > 0, count


class DistrictChain(BaseChain):
    code = "district"
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
        """A `sessions` signal listing each screen format that has shows, or [] if none has."""
        async with self._fetcher() as fetcher:
            html = await fetcher.get(movie.url, ready_js=SHOWTIMES_READY_JS)
        is_open, count = sessions_open(html)
        if not is_open:
            return []
        screenings = extract_screenings(html)
        formats: list[dict[str, object]] = [
            {"label": label, "showtimes": n, "url": movie.url} for label, n in screenings.items()
        ]
        if not formats:  # times but no structured records: say so, with an unnamed format
            formats = [{"label": "", "showtimes": count, "url": movie.url}]
        return [Signal("sessions", {"formats": formats, "url": movie.url})]
