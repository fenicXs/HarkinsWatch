"""check_chain: the snapshot guard, new-release alerts and watch stages, on a real database."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.db import queue
from showwatch.db.repositories import catalog, movies, watches
from showwatch.worker.handlers import WorkerEnv, check_chain
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, add_watch, approved_user, listing, many

Factory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


async def _scrape(env: WorkerEnv, listings: list, *, city: str = "phx"):  # type: ignore[no-untyped-def]
    async with env.session_factory() as session:
        chain = await catalog.get_chain_by_code(session, "harkins")
        found = await catalog.get_city_by_code(session, city)
    assert chain and found
    return await check_chain.process_scrape(env, chain, found, listings, now=NOW)


async def _movie_count(env: WorkerEnv) -> int:
    async with env.session_factory() as session:
        phx = await catalog.get_city_by_code(session, "phx")
        assert phx
        return len(await movies.list_for_city(session, phx.id))


# --- the baseline and the snapshot guard ------------------------------------------------------


async def test_the_first_scrape_stores_movies_and_sends_no_new_release_alerts(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    result = await _scrape(worker_env, many(5))
    assert (result.first_run, result.suspect, result.movie_count) == (True, False, 5)
    assert await _movie_count(worker_env) == 5
    assert notifier.sent == []


async def test_a_later_scrape_alerts_approved_users_in_the_city_about_a_new_release(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    await approved_user(factory, 1)  # approved, Phoenix
    await approved_user(factory, 2, city="blr")  # other city
    await approved_user(factory, 3, status="pending")  # not approved
    await approved_user(factory, 4, city=None)  # no city chosen
    await _scrape(worker_env, many(5))

    result = await _scrape(worker_env, [*many(5), listing("new-film", title="New Film")])

    assert result.new_releases == ["New Film"]
    assert notifier.to(1) == ["New at Harkins: New Film\nhttps://www.harkins.com/movies/new-film"]
    assert notifier.sent_chats() == {1}  # nobody else was told


async def test_the_same_scrape_twice_alerts_only_once(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    await _scrape(worker_env, many(5))
    bigger = [*many(5), listing("new-film")]
    await _scrape(worker_env, bigger)
    await _scrape(worker_env, bigger)
    assert len(notifier.to(1)) == 1


async def test_a_coming_soon_movie_going_on_sale_is_a_new_release(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    await _scrape(worker_env, [*many(5), listing("dune", "coming_soon", "Dune")])
    assert notifier.sent == []  # listed as coming soon: not a release yet

    result = await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])
    assert result.new_releases == ["Dune"]
    assert "New at Harkins: Dune" in notifier.to(1)[0]


async def test_a_broken_scrape_is_ignored_and_the_baseline_survives(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    await _scrape(worker_env, many(40))

    empty = await _scrape(worker_env, [])  # the page loaded but listed nothing
    half = await _scrape(worker_env, many(10))  # under half of 40

    assert (empty.suspect, half.suspect) == (True, True)
    assert await _movie_count(worker_env) == 40  # nothing was written
    assert notifier.sent == []

    # The next normal scrape compares with the last GOOD one, so this is not a "new" flood.
    recovered = await _scrape(worker_env, many(40))
    assert (recovered.suspect, recovered.first_run, recovered.new_releases) == (False, False, [])


async def test_a_zero_movie_first_scrape_is_not_a_baseline(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    first = await _scrape(worker_env, [])
    assert first.suspect
    real = await _scrape(worker_env, many(20))
    assert real.first_run  # still the first good one: no alert flood for 20 "new" movies
    assert notifier.sent == []


# --- watches ----------------------------------------------------------------------------------


async def test_a_watch_for_an_unlisted_title_attaches_when_the_match_is_clear(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    watch_id = await add_watch(factory, user, "dune messiah")
    await _scrape(worker_env, many(5))
    assert notifier.sent == []  # still unlisted: nothing to say

    await _scrape(worker_env, [*many(5), listing("dm", "coming_soon", "Dune: Messiah")])

    async with factory() as session:
        item = await watches.get(session, watch_id)
    assert item is not None
    assert item.movie_id is not None
    assert item.stage == "listed"
    assert item.fast_until is not None
    assert "Dune: Messiah is now listed at Harkins (coming soon)" in notifier.to(1)[0]


async def test_a_watch_does_not_attach_to_a_close_spelling_or_an_unclear_match(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    vague = await add_watch(factory, user, "dune")
    typo = await add_watch(factory, user, "moanna")
    await _scrape(worker_env, many(5))

    await _scrape(
        worker_env,
        [
            *many(5),
            listing("d1", "coming_soon", "Dune: Part Three"),
            listing("d2", "coming_soon", "Dune: Messiah"),  # two titles contain "dune"
            listing("moana", "coming_soon", "Moana"),  # a near spelling, not a match
        ],
    )

    async with factory() as session:
        assert (await watches.get(session, vague)).movie_id is None  # type: ignore[union-attr]
        assert (await watches.get(session, typo)).movie_id is None  # type: ignore[union-attr]
    assert notifier.sent == []


async def test_an_exact_title_wins_when_several_titles_contain_the_words(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    watch_id = await add_watch(factory, user, "Dune")
    await _scrape(worker_env, many(5))
    await _scrape(
        worker_env,
        [
            *many(5),
            listing("d", "coming_soon", "Dune"),
            listing("di", "coming_soon", "Dune (IMAX 70mm)"),
        ],
    )
    async with factory() as session:
        item = await watches.get(session, watch_id)
        chosen = await movies.get(session, item.movie_id) if item and item.movie_id else None  # type: ignore[arg-type]
    assert chosen is not None
    assert chosen.title == "Dune"


async def test_a_watched_movie_going_on_sale_alerts_once_with_a_book_button_then_finishes(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    watch_id = await add_watch(factory, user, "Dune")
    await _scrape(worker_env, many(5))
    await _scrape(worker_env, [*many(5), listing("dune", "coming_soon", "Dune")])
    notifier.sent.clear()

    result = await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])

    assert result.watch_events == [("Dune", "sessions")]
    assert notifier.to(1) == ["Dune is now playing at Harkins! Tap to pick a showtime."]
    ((label, data),) = notifier.buttons_to(1)
    assert label == "Book seats"
    assert data.startswith("pick:")
    async with factory() as session:
        assert (await watches.get(session, watch_id)).stage == "done"  # type: ignore[union-attr]

    await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])
    assert len(notifier.to(1)) == 1  # not again


async def test_a_user_alerted_through_a_watch_is_not_also_sent_the_generic_alert(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    watcher = await approved_user(factory, 1)
    await approved_user(factory, 2)  # watches nothing
    await add_watch(factory, watcher, "Dune")
    await _scrape(worker_env, many(5))

    result = await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])

    assert result.new_releases == ["Dune"]
    assert len(notifier.to(1)) == 1
    assert "is now playing" in notifier.to(1)[0]  # the watch alert only
    assert notifier.to(2) == ["New at Harkins: Dune\nhttps://www.harkins.com/movies/dune"]


async def test_a_watch_alerts_even_on_the_very_first_scrape(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    await add_watch(factory, user, "Dune")
    await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])
    assert len(notifier.to(1)) == 1  # the user asked for it, so no baseline silence


async def test_a_watch_whose_owner_is_no_longer_approved_is_left_alone(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1, status="denied")
    await add_watch(factory, user, "Dune")
    await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])
    assert notifier.sent == []


async def test_a_watch_alert_that_failed_to_send_is_delivered_on_a_later_scrape(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    await add_watch(factory, user, "Dune")
    await _scrape(worker_env, many(5))
    on_sale = [*many(5), listing("dune", "now_showing", "Dune")]

    notifier.unreachable.add(1)  # Telegram refuses the message
    await _scrape(worker_env, on_sale)
    assert notifier.sent == []
    async with factory() as session:
        item = (await watches.list_for_user(session, user.id))[0]
    assert item.stage == "done"  # the watch moved on, but the user was never told

    notifier.unreachable.clear()
    result = await _scrape(worker_env, on_sale)  # the next check still owes the alert
    assert result.watch_events == [("Dune", "sessions")]
    assert notifier.to(1) == ["Dune is now playing at Harkins! Tap to pick a showtime."]

    await _scrape(worker_env, on_sale)
    assert len(notifier.to(1)) == 1  # delivered once, then never again


async def test_an_undelivered_listed_alert_is_dropped_once_the_movie_is_on_sale(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    factory = worker_env.session_factory
    user = await approved_user(factory, 1)
    await add_watch(factory, user, "Dune")
    await _scrape(worker_env, many(5))

    notifier.unreachable.add(1)
    await _scrape(worker_env, [*many(5), listing("dune", "coming_soon", "Dune")])  # "listed" fails
    notifier.unreachable.clear()
    await _scrape(worker_env, [*many(5), listing("dune", "now_showing", "Dune")])

    assert notifier.to(1) == ["Dune is now playing at Harkins! Tap to pick a showtime."]


# --- the job handler --------------------------------------------------------------------------


async def test_the_job_handler_scrapes_through_the_registry(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    await approved_user(worker_env.session_factory, 1)
    chain.movies = many(6)
    async with worker_env.session_factory() as session, session.begin():
        harkins, phx = (
            await catalog.get_chain_by_code(session, "harkins"),
            await catalog.get_city_by_code(session, "phx"),
        )
        assert harkins and phx
        job = await queue.enqueue(
            session,
            "check_chain",
            {"chain": "harkins", "city": "phx"},
            chain_id=harkins.id,
            city_id=phx.id,
        )

    summary = await check_chain.run(worker_env, job)

    assert summary == {
        "suspect": False,
        "first_run": True,
        "movies": 6,
        "new_releases": [],
        "alerts_sent": 0,
    }


async def test_the_job_handler_rejects_an_unknown_city(
    worker_env: WorkerEnv, chain: FakeChain
) -> None:
    async with worker_env.session_factory() as session, session.begin():
        job = await queue.enqueue(
            session, "search_showtimes", {"chain": "harkins", "city": "nowhere"}
        )
    import pytest

    with pytest.raises(LookupError):
        await check_chain.run(worker_env, job)
