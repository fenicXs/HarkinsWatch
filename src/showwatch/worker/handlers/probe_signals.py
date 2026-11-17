"""probe_signals: for one watch-only chain, check whether tickets opened for the movies users wait on.

One job covers a chain, so the chain is only visited one page at a time. For each watch that is
`listed` the job finds the movie on this chain (the watch's own movie, or the same title on this
chain's list), loads its showtimes page once per movie, and, when sessions exist, records the
`sessions` signal, finishes the watch and sends the alert with a link. A block (403, 429,
challenge page) is raised as `BlockedError`: the runner puts the job back after the back-off.
"""

from typing import Any

import structlog

from showwatch.db.repositories import catalog, movies, signals, watches
from showwatch.domain.models import Capability, City, Job, Movie, WatchItem
from showwatch.domain.title_match import match_for_watch
from showwatch.worker.handlers import WorkerEnv
from showwatch.worker.handlers.check_chain import deliver_owed

log = structlog.get_logger(__name__)


def movie_on_chain(item: WatchItem, chain_movies: list[Movie]) -> Movie | None:
    """The movie this watch means on this chain: its own row, else the same title here."""
    for movie in chain_movies:
        if movie.id == item.movie_id:
            return movie
    title = match_for_watch(item.query, [m.title for m in chain_movies])
    return next((m for m in chain_movies if m.title == title), None) if title else None


async def _probe_city(env: WorkerEnv, chain_code: str, chain_id: int, city: City) -> dict[str, int]:
    chain = env.registry.require(chain_code, Capability.SIGNALS)
    async with env.session_factory() as session:
        if not await catalog.list_theatres(session, chain_id, city.id):
            return {"probed": 0, "opened": 0}
        items = await watches.list_by_stage(session, ["listed"], city_id=city.id)
        chain_movies = [
            m
            for m in await movies.list_for_city(session, city.id, current_only=True)
            if m.chain_id == chain_id
        ]
    groups: dict[int, tuple[Movie, list[WatchItem]]] = {}
    for item in items:
        movie = movie_on_chain(item, chain_movies)
        if movie is not None:
            groups.setdefault(movie.id, (movie, []))[1].append(item)

    probed = opened = 0
    for movie, group in groups.values():
        seen = await chain.probe_signals(movie)  # may raise BlockedError: the runner backs off
        probed += 1
        found = next((s for s in seen if s.stage == "sessions"), None)
        if found is None:
            continue
        evidence: dict[str, Any] = {"chain": chain_code, "movie_id": movie.id, **found.evidence}
        async with env.session_factory() as session, session.begin():
            for item in group:
                await signals.record(session, item.id, "sessions", evidence)
                await watches.set_stage(session, item.id, "done")  # alert delivered below
        opened += 1
        log.info("sessions_opened", chain=chain_code, movie=movie.title, watchers=len(group))
    if opened:
        await deliver_owed(env, city)
    return {"probed": probed, "opened": opened}


async def run(env: WorkerEnv, job: Job) -> dict[str, Any]:
    chain_code = job.payload["chain"]
    async with env.session_factory() as session:
        info = await catalog.get_chain_by_code(session, chain_code)
        cities = await catalog.list_cities(session)
    if info is None:
        raise LookupError(f"unknown chain in job {job.id}: {chain_code}")
    totals = {"probed": 0, "opened": 0}
    for city in cities:
        counts = await _probe_city(env, chain_code, info.id, city)
        totals["probed"] += counts["probed"]
        totals["opened"] += counts["opened"]
    return totals
