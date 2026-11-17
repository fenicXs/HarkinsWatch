"""Live check of the watch-only chains (BookMyShow, District): list movies, probe two movies.

    uv run python scripts/smoke_watch.py [--chain bms|district] [--channel chrome] [--headless]
    docker compose exec -T worker python - --chain bms < scripts/smoke_watch.py

Loads a handful of public pages (the movie list and two showtimes pages per chain). Uses the
database only for the request budget and back-off state. Never run in CI.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from showwatch.chains.base import BaseChain, BlockedError
from showwatch.chains.bms.scraper import BookMyShowChain
from showwatch.chains.district.scraper import DistrictChain
from showwatch.chains.politeness import Politeness
from showwatch.core.db import create_engine, create_sessionmaker
from showwatch.core.settings import get_settings
from showwatch.domain.models import City, Movie

CITY = City(id=2, code="blr", name="Bengaluru", tz="Asia/Kolkata")


def movie_for(listing_url: str, external_id: str, title: str) -> Movie:
    now = datetime.now(UTC)
    return Movie(
        id=1,
        chain_id=1,
        city_id=2,
        external_id=external_id,
        title=title,
        normalized_title=title.lower(),
        url=listing_url,
        status="coming_soon",
        first_seen_at=now,
        last_seen_at=now,
        released_at=None,
    )


async def check(chain: BaseChain, wanted: list[str]) -> None:
    print(f"== {chain.code}")
    listings = await chain.list_movies(CITY)
    print(f"movies listed: {len(listings)}; first: {[m.title for m in listings[:3]]}")
    for needle in wanted:
        match = next((m for m in listings if needle.lower() in m.title.lower()), None)
        if match is None:
            print(f"  {needle}: not on the list")
            continue
        movie = movie_for(match.url, match.external_id, match.title)
        signals = await chain.probe_signals(movie)
        print(
            f"  {match.title}: {'OPEN ' + str(signals[0].evidence) if signals else 'no sessions'}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", choices=["bms", "district"])
    parser.add_argument("--channel", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--movie", action="append", default=None, help="title to probe")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    politeness = Politeness(create_sessionmaker(engine), settings.chain_request_budget_per_min)
    kwargs = {"channel": args.channel, "headless": args.headless}
    chains: list[BaseChain] = []
    if args.chain in (None, "bms"):
        chains.append(BookMyShowChain(politeness, **kwargs))  # type: ignore[arg-type]
    if args.chain in (None, "district"):
        chains.append(DistrictChain(politeness, **kwargs))  # type: ignore[arg-type]
    wanted = args.movie or ["Ramayana", "Avengers"]
    try:
        for chain in chains:
            try:
                await check(chain, wanted)
            except BlockedError as error:
                print(f"BLOCKED: {error}")
    finally:
        await engine.dispose()


asyncio.run(main())
