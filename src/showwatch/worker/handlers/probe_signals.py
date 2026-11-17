"""probe_signals: for one watch-only chain, check whether tickets opened in a format someone wants.

One job covers a chain, so the chain is only visited one page at a time. For each watch that is
`listed` (or already told by another chain) the job finds the movie on this chain (the watch's own
movie, or the same title here), loads its showtimes once per movie and format preference, and
decides which of the formats found satisfy the watch (`domain/formats.py`; a label the rules cannot
place goes to the LLM). When one does, it records a `sessions` signal for that chain and sends an
alert with a link. A watch stays on until every chain has told it, so BookMyShow and District
each get their say. A block (403, 429, challenge page) is raised as `BlockedError`: the runner
puts the job back after the back-off.
"""

from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError

from showwatch.chains.base import BlockedError
from showwatch.db.repositories import catalog, movies, signals, watches
from showwatch.domain.formats import FormatPref, classify
from showwatch.domain.models import Capability, City, Job, Movie, WatchItem
from showwatch.domain.title_match import match_for_watch
from showwatch.llm.tasks import format_label
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


async def matching_formats(
    env: WorkerEnv, formats: list[dict[str, Any]], pref: FormatPref
) -> list[dict[str, Any]]:
    """The formats found on a page that satisfy the preference.

    The rules decide first. Only labels they cannot place are put to the LLM, once each; with
    no LLM, or if it fails, such a label does not count (an unsure alert is not sent).
    """
    # A page that names no format cannot be checked: tell the person (the alert says so)
    # rather than stay silent about a possible opening.
    unnamed = [f for f in formats if not str(f.get("label") or "").strip()]
    named = [f for f in formats if str(f.get("label") or "").strip()]
    keep = [f for f in named if classify(str(f["label"]), pref) == "match"] + unnamed
    unknown = [f for f in named if classify(str(f["label"]), pref) == "unknown"]
    if unknown and env.llm is not None and env.llm.configured:
        verdicts = await format_label.judge(env.llm, [str(f["label"]) for f in unknown], pref)
        keep += [f for f in unknown if verdicts.get(str(f["label"]))]
    return keep


def _formats_of(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """The formats a signal reported; a chain that reported none counts as one unnamed format."""
    found = evidence.get("formats")
    if isinstance(found, list) and found:
        return [f for f in found if isinstance(f, dict)]
    return [{"label": "", "showtimes": evidence.get("showtimes"), "url": evidence.get("url")}]


async def _signal_chains(env: WorkerEnv, session: Any, city: City) -> dict[str, int]:
    """The chains that can report signals for this city (code -> id)."""
    served: dict[str, int] = {}
    for chain in await catalog.list_enabled_chains(session):
        if chain.code not in env.registry.codes():
            continue
        if Capability.SIGNALS not in env.registry.get(chain.code).capabilities:
            continue
        if await catalog.list_theatres(session, chain.id, city.id):
            served[chain.code] = chain.id
    return served


async def _probe_city(env: WorkerEnv, chain_code: str, chain_id: int, city: City) -> dict[str, int]:
    chain = env.registry.require(chain_code, Capability.SIGNALS)
    async with env.session_factory() as session:
        if not await catalog.list_theatres(session, chain_id, city.id):
            return {"probed": 0, "opened": 0}
        items = await watches.list_by_stage(session, ["listed", "sessions"], city_id=city.id)
        heard = await signals.sessions_sources(session, [i.id for i in items])
        served = await _signal_chains(env, session, city)
        city_movies = await movies.list_for_city(session, city.id, current_only=True)
        chain_movies = [m for m in city_movies if m.chain_id == chain_id]
    groups: dict[tuple[int, str | None], tuple[Movie, FormatPref, list[WatchItem]]] = {}
    for item in items:
        if chain_code in heard.get(item.id, set()):
            continue  # this chain already told them
        movie = movie_on_chain(item, chain_movies)
        if movie is not None:
            pref = FormatPref.from_json(item.format_pref)
            groups.setdefault((movie.id, pref.to_json()), (movie, pref, []))[2].append(item)

    probed = opened = 0
    try:
        for movie, pref, group in groups.values():
            try:
                seen = await chain.probe_signals(movie, pref)
            except BlockedError:
                raise  # the site pushed back: stop here and let the runner back off
            except Exception:
                # One broken page must not starve the other movies or hold back alerts.
                log.exception("probe_failed", chain=chain_code, movie=movie.title)
                continue
            probed += 1
            found = next((s for s in seen if s.stage == "sessions"), None)
            if found is None:
                continue
            matched = await matching_formats(env, _formats_of(found.evidence), pref)
            if not matched:
                log.info("sessions_not_wanted", chain=chain_code, movie=movie.title)
                continue
            url = matched[0].get("url") or found.evidence.get("url")
            evidence: dict[str, Any] = {
                "chain": chain_code,
                "movie_id": movie.id,
                "url": url,
                "formats": matched,
            }
            try:
                async with env.session_factory() as session, session.begin():
                    for item in group:
                        await signals.record(
                            session, item.id, "sessions", evidence, source=chain_code
                        )
                        told = heard.get(item.id, set()) | {chain_code}
                        carried = {
                            code
                            for code, cid in served.items()
                            if movie_on_chain(item, [m for m in city_movies if m.chain_id == cid])
                        } | {chain_code}
                        await watches.set_stage(
                            session, item.id, "done" if carried <= told else "sessions"
                        )
            except (IntegrityError, LookupError):
                # A watch was removed or replaced while the page loaded: nothing to record.
                log.warning("watch_gone_mid_probe", chain=chain_code, movie=movie.title)
                continue
            opened += 1
            log.info("sessions_opened", chain=chain_code, movie=movie.title, watchers=len(group))
    finally:
        if opened:  # a block later in the loop must not delay alerts already decided
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
