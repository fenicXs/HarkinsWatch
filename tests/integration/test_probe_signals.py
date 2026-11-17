"""probe_signals and its scheduling: the listed -> sessions stages for a watch-only chain."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llmfakes import FakeLLM
from showwatch.chains.base import BaseChain, BlockedError
from showwatch.chains.politeness import Politeness
from showwatch.chains.registry import ChainRegistry
from showwatch.core.settings import Settings
from showwatch.db import queue
from showwatch.db.repositories import catalog, signals, watches
from showwatch.domain.formats import FormatPref
from showwatch.domain.models import Capability, City, Job, Movie, MovieListing, Signal
from showwatch.worker.handlers import WorkerEnv, check_chain, probe_signals
from showwatch.worker.scheduler import tick
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, add_watch, approved_user

Factory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PAGE = "https://in.bookmyshow.com/movies/bengaluru/dune/ET00000001"
TICKETS = "https://in.bookmyshow.com/movies/bang/dune-bengaluru/buytickets/ET00000001/20261117"


class FakeSignalChain(BaseChain):
    """A watch-only chain: it lists movies and says whether sessions exist."""

    capabilities = Capability.WATCH | Capability.SIGNALS

    def __init__(self, code: str = "bms") -> None:
        self.code = code
        self.movies = [MovieListing("ET00000001", "Dune Part Three", PAGE, "coming_soon")]
        self.opened: set[str] = set()
        self.error: Exception | None = None
        self.probed: list[str] = []
        self.formats: list[dict] | None = None  # type: ignore[type-arg]
        self.prefs: list[object] = []

    async def list_movies(self, city: City) -> list[MovieListing]:
        return list(self.movies)

    async def probe_signals(self, movie: Movie, pref: object = None) -> list[Signal]:
        if self.error:
            raise self.error
        self.probed.append(movie.external_id)
        self.prefs.append(pref)
        if movie.external_id in self.opened:
            evidence: dict = {"url": TICKETS, "showtimes": 12}  # type: ignore[type-arg]
            if self.formats is not None:
                evidence["formats"] = self.formats
            return [Signal("sessions", evidence)]
        return []


def _env(factory: Factory, notifier: FakeNotifier, *chains: BaseChain) -> WorkerEnv:
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://unused")  # type: ignore[call-arg]
    registry = ChainRegistry()
    for chain in chains:
        registry.register(chain)  # type: ignore[arg-type]
    return WorkerEnv(
        session_factory=factory,
        notifier=notifier,
        registry=registry,
        politeness=Politeness(factory, per_minute=6000),
        settings=settings,
    )


async def _list_in_bangalore(env: WorkerEnv, chain: FakeSignalChain) -> None:
    """Run the watch-only chain's movie check, which marks matching watches `listed`."""
    async with env.session_factory() as session:
        info = await catalog.get_chain_by_code(session, chain.code)
        city = await catalog.get_city_by_code(session, "blr")
    assert info and city
    await check_chain.process_scrape(env, info, city, chain.movies, now=NOW)


async def _probe_job(env: WorkerEnv, code: str = "bms") -> Job:
    async with env.session_factory() as session, session.begin():
        info = await catalog.get_chain_by_code(session, code)
        assert info
        return await queue.enqueue(
            session, "probe_signals", {"chain": code}, chain_id=info.id, run_after=NOW
        )


async def _stage(env: WorkerEnv, watch_id: int) -> str:
    async with env.session_factory() as session:
        item = await watches.get(session, watch_id)
    assert item
    return item.stage


@pytest.fixture
async def setup(
    session_factory: Factory, notifier: FakeNotifier
) -> tuple[WorkerEnv, FakeSignalChain, int]:
    chain = FakeSignalChain()
    env = _env(session_factory, notifier, FakeChain(), chain)
    await approved_user(session_factory, 1, city="blr")
    watch_id = await add_watch(session_factory, await _user(session_factory), "dune", city="blr")
    await _list_in_bangalore(env, chain)
    return env, chain, watch_id


async def _user(factory: Factory):  # type: ignore[no-untyped-def]
    from showwatch.db.repositories import users

    async with factory() as session:
        user = await users.get_by_telegram_id(session, 1)
    assert user
    return user


async def test_listing_a_watched_movie_moves_the_watch_to_listed_and_alerts(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, _, watch_id = setup
    assert await _stage(env, watch_id) == "listed"
    assert notifier.to(1) == [
        "Dune Part Three is now listed at BookMyShow (coming soon). "
        "I'll tell you the moment tickets open."
    ]


async def test_a_probe_with_no_sessions_changes_nothing(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    result = await probe_signals.run(env, await _probe_job(env))
    assert result == {"probed": 1, "opened": 0}
    assert chain.probed == ["ET00000001"]
    assert await _stage(env, watch_id) == "listed"
    assert len(notifier.to(1)) == 1  # still only the "listed" alert


async def test_sessions_opening_alerts_once_with_a_link_and_no_booking_button(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    chain.opened.add("ET00000001")

    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 1}
    assert await _stage(env, watch_id) == "done"
    assert (
        notifier.to(1)[-1]
        == f"Tickets are open for Dune Part Three on BookMyShow! Book here: {TICKETS}"
    )
    assert notifier.buttons_to(1) == []  # a link to book by hand, no booking button
    async with env.session_factory() as session:
        assert await signals.stages_seen(session, watch_id) == ["listed", "sessions"]

    # A second probe finds nothing to look at (the watch is done) and sends nothing new.
    again = await probe_signals.run(env, await _probe_job(env))
    assert again == {"probed": 0, "opened": 0}
    assert len(notifier.to(1)) == 2


async def test_each_watcher_of_the_movie_is_alerted_once(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, _ = setup
    await approved_user(env.session_factory, 2, city="blr")
    from showwatch.db.repositories import users

    async with env.session_factory() as session:
        second = await users.get_by_telegram_id(session, 2)
    assert second
    await add_watch(env.session_factory, second, "dune part three", city="blr")
    await _list_in_bangalore(env, chain)  # attaches the second watch and marks it listed
    chain.opened.add("ET00000001")

    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 1}  # one page load serves both watchers
    assert sum("Tickets are open" in m for m in notifier.to(1)) == 1
    assert sum("Tickets are open" in m for m in notifier.to(2)) == 1


async def test_a_block_propagates_so_the_runner_can_back_off(
    setup: tuple[WorkerEnv, FakeSignalChain, int],
) -> None:
    env, chain, watch_id = setup
    chain.error = BlockedError("challenge page")
    with pytest.raises(BlockedError):
        await probe_signals.run(env, await _probe_job(env))
    assert await _stage(env, watch_id) == "listed"  # nothing was lost


# --- scheduling -------------------------------------------------------------------------------


async def test_no_probe_is_queued_without_a_listed_watch(
    session_factory: Factory, notifier: FakeNotifier
) -> None:
    env = _env(session_factory, notifier, FakeSignalChain())
    result = await tick(env, now=NOW)
    assert ("bms", "signals") not in result.enqueued


async def test_a_listed_watch_queues_one_probe_per_chain_then_waits_the_interval(
    setup: tuple[WorkerEnv, FakeSignalChain, int],
) -> None:
    env, _, _ = setup
    first = await tick(env, now=NOW)
    assert first.enqueued.count(("bms", "signals")) == 1
    again = await tick(env, now=NOW)  # one is already queued
    assert ("bms", "signals") not in again.enqueued

    async with env.session_factory() as session, session.begin():
        info = await catalog.get_chain_by_code(session, "bms")
        assert info
        claimed = await queue.claim(session, "w1", now=NOW + timedelta(seconds=1))
        while claimed is not None and claimed.kind != "probe_signals":
            await queue.complete(session, claimed.id, "w1", {})
            claimed = await queue.claim(session, "w1", now=NOW + timedelta(seconds=1))
        assert claimed is not None
        await queue.complete(session, claimed.id, "w1", {})

    later = await tick(env, now=NOW + timedelta(seconds=1))
    assert ("bms", "signals") in later.enqueued
    async with env.session_factory() as session:
        row = (
            await session.execute(
                text(
                    "select (select run_after from jobs where kind='probe_signals' "
                    "and status='queued') as due, "
                    "(select max(updated_at) from jobs where kind='probe_signals' "
                    "and status='done') as finished"
                )
            )
        ).one()
    # The next look is about SIGNAL_INTERVAL_S (300 s) after the last one, give or take 20%.
    assert 240 <= (row.due - row.finished).total_seconds() <= 361


async def test_one_broken_page_does_not_stop_the_other_movies_or_their_alerts(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await approved_user(env.session_factory, 2, city="blr")
    from showwatch.db.repositories import users

    async with env.session_factory() as session:
        second = await users.get_by_telegram_id(session, 2)
    assert second
    await add_watch(env.session_factory, second, "other film", city="blr")
    chain.movies = [
        *chain.movies,
        MovieListing(
            "ET00000002", "Other Film", PAGE.replace("ET00000001", "ET00000002"), "coming_soon"
        ),
    ]
    await _list_in_bangalore(env, chain)
    chain.opened.add("ET00000002")
    original = chain.probe_signals

    async def flaky(movie: Movie, pref: object = None) -> list[Signal]:
        if movie.external_id == "ET00000001":
            raise RuntimeError("HTTP 500")
        return await original(movie, pref)

    chain.probe_signals = flaky  # type: ignore[method-assign]
    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 1}
    assert await _stage(env, watch_id) == "listed"  # the broken one is tried again next time
    assert any("Tickets are open for Other Film" in m for m in notifier.to(2))


# --- both sites, and the formats a person wants ---------------------------------------------------

THREE_D = [{"label": "MS INFINITY VISION 3D", "showtimes": 40, "url": "https://d.test/3d"}]
TWO_D = [{"label": "2D", "showtimes": 12, "url": "https://d.test/2d"}]
WANT_2D_NO_3D = FormatPref.of(["2D", "MS - Infinity Vision"], ["3D"]).to_json()


async def _prefer(env: WorkerEnv, watch_id: int, pref_json: str | None) -> None:
    async with env.session_factory() as session, session.begin():
        await watches.set_format_pref(session, watch_id, pref_json)


async def _with_district(env: WorkerEnv, notifier: FakeNotifier) -> FakeSignalChain:
    district = FakeSignalChain("district")
    env.registry.register(district)  # type: ignore[arg-type]
    async with env.session_factory() as session, session.begin():
        await session.execute(text("update chains set enabled = true where code = 'district'"))
    await _list_in_bangalore(env, district)
    return district


async def test_each_site_that_opens_alerts_once_and_the_watch_ends_when_all_have(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, bms, watch_id = setup
    district = await _with_district(env, notifier)
    district.opened.add("ET00000001")

    await probe_signals.run(env, await _probe_job(env, "district"))
    assert await _stage(env, watch_id) == "sessions"  # BookMyShow has not opened yet
    assert notifier.to(1)[-1].startswith("Tickets are open for Dune Part Three on District")

    await probe_signals.run(env, await _probe_job(env, "district"))  # District is not asked again
    assert district.probed == ["ET00000001"]

    bms.opened.add("ET00000001")
    await probe_signals.run(env, await _probe_job(env, "bms"))
    assert await _stage(env, watch_id) == "done"
    assert notifier.to(1)[-1].startswith("Tickets are open for Dune Part Three on BookMyShow")
    assert sum("Tickets are open" in m for m in notifier.to(1)) == 2


async def test_a_3d_only_opening_does_not_alert_someone_who_wants_2d(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await _prefer(env, watch_id, WANT_2D_NO_3D)
    chain.opened.add("ET00000001")
    chain.formats = THREE_D

    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 0}
    assert await _stage(env, watch_id) == "listed"  # still waiting for the 2D one
    assert not any("Tickets are open" in m for m in notifier.to(1))
    assert chain.prefs == [
        FormatPref.of(["2D", "MS - Infinity Vision"], ["3D"])
    ]  # the chain is told


async def test_a_2d_opening_alerts_and_names_the_matching_formats_only(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await _prefer(env, watch_id, WANT_2D_NO_3D)
    chain.opened.add("ET00000001")
    chain.formats = [*THREE_D, *TWO_D]

    await probe_signals.run(env, await _probe_job(env))

    message = notifier.to(1)[-1]
    assert "(2D: 12 shows)" in message
    assert "3D" not in message
    assert await _stage(env, watch_id) == "done"


async def test_no_preference_still_alerts_on_any_opening(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, _ = setup
    chain.opened.add("ET00000001")
    chain.formats = THREE_D

    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 1}


async def test_a_label_the_rules_cannot_place_is_put_to_the_llm(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await _prefer(env, watch_id, WANT_2D_NO_3D)
    chain.opened.add("ET00000001")
    chain.formats = [{"label": "BRAND NEW FORMAT", "showtimes": 5, "url": "https://d.test/x"}]
    env = replace(env, llm=FakeLLM(labels_reply="[1]"))

    result = await probe_signals.run(env, await _probe_job(env))

    assert result["opened"] == 1


async def test_an_unknown_label_without_an_llm_is_not_alerted(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await _prefer(env, watch_id, WANT_2D_NO_3D)
    chain.opened.add("ET00000001")
    chain.formats = [{"label": "IMAX", "showtimes": 5, "url": "https://d.test/x"}]

    result = await probe_signals.run(env, await _probe_job(env))  # env has no LLM

    assert result["opened"] == 0


async def test_a_page_that_names_no_format_still_alerts_and_says_so(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    await _prefer(env, watch_id, WANT_2D_NO_3D)
    chain.opened.add("ET00000001")
    chain.formats = [{"label": "", "showtimes": 9, "url": "https://d.test/x"}]

    await probe_signals.run(env, await _probe_job(env))

    assert "(format not stated by the site)" in notifier.to(1)[-1]


async def test_a_watch_ends_when_every_site_that_carries_the_movie_has_told_it(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, bms, watch_id = setup
    district = FakeSignalChain("district")
    district.movies = []  # District does not list this movie at all
    env.registry.register(district)  # type: ignore[arg-type]
    async with env.session_factory() as session, session.begin():
        await session.execute(text("update chains set enabled = true where code = 'district'"))
    await _list_in_bangalore(env, district)
    bms.opened.add("ET00000001")

    await probe_signals.run(env, await _probe_job(env))

    assert await _stage(env, watch_id) == "done"  # nothing to wait for from District


async def test_a_watch_removed_while_its_page_loaded_does_not_break_the_probe(
    setup: tuple[WorkerEnv, FakeSignalChain, int], notifier: FakeNotifier
) -> None:
    env, chain, watch_id = setup
    chain.opened.add("ET00000001")
    original = chain.probe_signals

    async def probe_then_lose_the_watch(movie: Movie, pref: object = None) -> list[Signal]:
        result = await original(movie, pref)
        async with env.session_factory() as session, session.begin():
            await session.execute(text("delete from watch_items"))
        return result

    chain.probe_signals = probe_then_lose_the_watch  # type: ignore[method-assign]

    result = await probe_signals.run(env, await _probe_job(env))

    assert result == {"probed": 1, "opened": 0}
