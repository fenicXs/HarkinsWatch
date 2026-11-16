"""check_chain: scrape one chain for one city, store what changed, alert the right people.

Rules (docs/plan.md, M3):
- A scrape that returns nothing, or under half of the last good count, is `suspect`: it is
  recorded and then ignored (no movies written, no alerts), so a broken page can never wipe
  the baseline or flood users with "new release" alerts.
- The first good scrape for a chain and city stores movies and sends no "new release" alerts.
- A "new release" is a movie inserted as now showing, or one that moved from coming soon to now
  showing. It goes to approved users in that city.
- A watch attaches to a listed movie when the match is clear (`match_for_watch`). Harkins has no
  early signals, so a watched title moves waiting -> listed (coming soon) -> done (now showing,
  alert sent). A user alerted through their watch is not also sent the generic alert.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from showwatch.bot import keyboards
from showwatch.db.repositories import alerts, catalog, movies, signals, users, watches
from showwatch.domain.models import (
    Capability,
    ChainInfo,
    City,
    Job,
    Movie,
    MovieListing,
    User,
)
from showwatch.domain.title_match import match_for_watch
from showwatch.notify import Buttons
from showwatch.worker.handlers import WorkerEnv

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WatchEvent:
    watch_item_id: int
    user: User
    movie: Movie
    stage: str  # "listed" or "sessions"


@dataclass(frozen=True)
class _Alert:
    user: User
    key: str
    text: str
    buttons: Buttons | None


@dataclass
class ScrapeResult:
    suspect: bool = False
    first_run: bool = False
    movie_count: int = 0
    new_releases: list[str] | None = None  # titles
    watch_events: list[tuple[str, str]] | None = None  # (title, stage)
    alerts_sent: int = 0


def _chat_id(user: User) -> int:
    return user.chat_id or user.telegram_user_id


async def _advance_watches(
    session: Any, env: WorkerEnv, city: City, now: datetime
) -> list[WatchEvent]:
    """Attach unmatched watches to listed movies, then move matched ones along their stages."""
    city_movies = await movies.list_for_city(session, city.id)
    titles = [m.title for m in city_movies]
    for item in await watches.list_unmatched(session, city.id):
        title = match_for_watch(item.query, titles)
        if title is not None:
            movie = next(m for m in city_movies if m.title == title)
            await watches.set_movie(session, item.id, movie.id)

    by_id = {m.id: m for m in city_movies}
    for item in await watches.list_by_stage(session, ["waiting", "listed"], city_id=city.id):
        listed = by_id.get(item.movie_id) if item.movie_id is not None else None
        user = await users.get(session, item.user_id)
        if listed is None or user is None or user.status != "approved":
            continue
        if listed.status == "now_showing":
            await signals.record(session, item.id, "sessions", {"movie_id": listed.id})
            await watches.set_stage(session, item.id, "done")  # alerted below, then finished
        elif item.stage == "waiting":
            await signals.record(session, item.id, "listed", {"movie_id": listed.id})
            fast_until = now + timedelta(hours=env.settings.fast_window_h)
            await watches.set_stage(session, item.id, "listed", fast_until=fast_until)

    # Alerts owed: this run's transitions plus any an earlier run decided but failed to deliver.
    events: list[WatchEvent] = []
    for item, stage in await watches.unalerted_stages(session, city.id):
        owner = await users.get(session, item.user_id)
        listed = by_id.get(item.movie_id) if item.movie_id is not None else None
        if owner is not None and owner.status == "approved" and listed is not None:
            events.append(WatchEvent(item.id, owner, listed, stage))
    return events


def _watch_alert(chain_name: str, event: WatchEvent) -> _Alert:
    title = event.movie.title
    key = f"watch_open:{event.watch_item_id}:{event.stage}"
    if event.stage == "listed":
        text = (
            f"{title} is now listed at {chain_name} (coming soon). "
            "I'll tell you the moment tickets open."
        )
        return _Alert(event.user, key, text, None)
    text = f"{title} is now playing at {chain_name}! Tap to pick a showtime."
    return _Alert(event.user, key, text, keyboards.book_seats(event.movie.id))


async def process_scrape(
    env: WorkerEnv,
    chain: ChainInfo,
    city: City,
    listings: list[MovieListing],
    *,
    now: datetime | None = None,
) -> ScrapeResult:
    """Store one scrape and send the alerts it earns. Works from listings, so it needs no browser."""
    now = now or datetime.now(UTC)
    chain_name = chain.code.title()
    pending: list[_Alert] = []
    async with env.session_factory() as session, session.begin():
        snapshot, first_run = await movies.record_snapshot(
            session, chain.id, city.id, len(listings)
        )
        if snapshot.status == "suspect":
            log.warning("scrape_suspect", chain=chain.code, city=city.code, count=len(listings))
            return ScrapeResult(suspect=True, movie_count=len(listings))

        released: list[Movie] = []
        for listing in listings:
            movie, change = await movies.upsert_movie(session, chain.id, city.id, listing)
            if movie.status == "now_showing" and change in ("inserted", "status_changed"):
                released.append(movie)

        events = await _advance_watches(session, env, city, now)
        pending.extend(_watch_alert(chain_name, e) for e in events)

        if not first_run:
            told_by_watch = {(e.user.id, e.movie.id) for e in events}
            for user in await users.list_approved_in_city(session, city.id):
                for movie in released:
                    if (user.id, movie.id) not in told_by_watch:
                        text = f"New at {chain_name}: {movie.title}\n{movie.url}"
                        pending.append(_Alert(user, f"new_release:{movie.id}", text, None))

    sent = await _deliver(env, pending)
    return ScrapeResult(
        first_run=first_run,
        movie_count=len(listings),
        new_releases=[] if first_run else [m.title for m in released],  # a baseline has none
        watch_events=[(e.movie.title, e.stage) for e in events],
        alerts_sent=sent,
    )


async def _deliver(env: WorkerEnv, pending: list[_Alert]) -> int:
    """Send each alert once. It counts as sent only after Telegram accepted it."""
    sent = 0
    for alert in pending:
        async with env.session_factory() as session:
            if await alerts.was_sent(session, alert.user.id, alert.key):
                continue
        if await env.notifier.send(_chat_id(alert.user), alert.text, alert.buttons):
            async with env.session_factory() as session, session.begin():
                await alerts.try_record(session, alert.user.id, alert.key)
            sent += 1
    return sent


async def run(env: WorkerEnv, job: Job) -> dict[str, Any]:
    chain_code, city_code = job.payload["chain"], job.payload["city"]
    chain = env.registry.require(chain_code, Capability.WATCH)
    async with env.session_factory() as session:
        info = await catalog.get_chain_by_code(session, chain_code)
        city = await catalog.get_city_by_code(session, city_code)
    if info is None or city is None:
        raise LookupError(f"unknown chain or city in job {job.id}: {chain_code}/{city_code}")
    listings = await chain.list_movies(city)
    result = await process_scrape(env, info, city, listings)
    return {
        "suspect": result.suspect,
        "first_run": result.first_run,
        "movies": result.movie_count,
        "new_releases": result.new_releases or [],
        "alerts_sent": result.alerts_sent,
    }
