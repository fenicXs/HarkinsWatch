"""Movie -> date -> (screen type) -> time: the bot side and the worker side together."""

from dataclasses import replace
from datetime import UTC, date, datetime

from sqlalchemy import text

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender, search
from showwatch.db import queue
from showwatch.db.repositories import catalog, movies
from showwatch.domain.models import Job, Movie
from showwatch.worker.handlers import WorkerEnv, search_showtimes
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, approved_user, listing, showtime

# 02:00 UTC on the 17th is 19:00 on the 16th in Phoenix: "today" there is Nov 16.
CLOCK = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
DAY = date(2026, 11, 17)
# The test sessions start at 13:00-22:00 Phoenix time on the 17th; look them up the evening before.
LOOKUP_TIME = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)


def _sender(telegram_user_id: int) -> Sender:
    return Sender(telegram_user_id=telegram_user_id, chat_id=telegram_user_id, display_name="Ann")


async def _movie(env: Env | WorkerEnv, slug: str = "moana", city: str = "phx") -> Movie:
    async with env.session_factory() as session, session.begin():
        chain = await catalog.get_chain_by_code(session, "harkins")
        found = await catalog.get_city_by_code(session, city)
        assert chain and found
        movie, _ = await movies.upsert_movie(
            session, chain.id, found.id, listing(slug, title=slug.title())
        )
        return movie


async def _job(env: Env | WorkerEnv, user_id: int, movie: Movie, day: date = DAY) -> Job:
    async with env.session_factory() as session, session.begin():
        return await queue.enqueue(
            session,
            "search_showtimes",
            {"user_id": user_id, "movie_id": movie.id, "day": day.isoformat()},
            chain_id=movie.chain_id,
        )


# --- the bot side -------------------------------------------------------------------------------


async def test_pick_offers_the_next_seven_days_from_todays_date_in_the_theatres_time_zone(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1)
    movie = await _movie(env)

    await search.handle_pick(clocked, _sender(1), movie.id)

    assert notifier.to(1) == ["Moana: pick a date."]
    buttons = notifier.buttons_to(1)
    assert len(buttons) == 7
    assert buttons[0] == ("Mon Nov 16", f"date:{movie.id}:2026-11-16")  # not Nov 17: that is UTC
    assert buttons[-1] == ("Sun Nov 22", f"date:{movie.id}:2026-11-22")


async def test_pick_refuses_a_movie_from_another_city_a_missing_movie_or_no_city(
    env: Env, notifier: FakeNotifier
) -> None:
    await approved_user(env.session_factory, 1, city="blr")
    await approved_user(env.session_factory, 2, city=None)
    phoenix_movie = await _movie(env)
    for telegram_id, movie_id in [(1, phoenix_movie.id), (1, 999_999), (2, phoenix_movie.id)]:
        await search.handle_pick(env, _sender(telegram_id), movie_id)
    assert notifier.to(1) == ["That movie is no longer available."] * 2
    assert notifier.to(2) == ["That movie is no longer available."]


async def test_tapping_a_date_queues_a_lookup_for_the_worker(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    user = await approved_user(env.session_factory, 1)
    movie = await _movie(env)

    await search.handle_date(clocked, _sender(1), movie.id, DAY)

    assert notifier.to(1) == ["Looking up showtimes…"]
    async with env.session_factory() as session:
        row = (await session.execute(text("select kind, payload, chain_id from jobs"))).one()
    assert row.kind == "search_showtimes"
    assert row.payload == {"user_id": user.id, "movie_id": movie.id, "day": "2026-11-17"}
    assert row.chain_id == movie.chain_id


async def test_a_date_outside_the_offered_week_is_refused_and_nothing_is_queued(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1)
    movie = await _movie(env)

    await search.handle_date(clocked, _sender(1), movie.id, date(2026, 11, 15))  # yesterday
    await search.handle_date(clocked, _sender(1), movie.id, date(2026, 11, 23))  # 8th day

    assert notifier.to(1) == ["That date is out of range. Tap a date from the list."] * 2
    async with env.session_factory() as session:
        assert (await session.scalar(text("select count(*) from jobs"))) == 0


async def test_a_date_for_a_movie_the_user_cannot_see_queues_nothing(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1, city="blr")
    movie = await _movie(env)  # a Phoenix movie
    await search.handle_date(clocked, _sender(1), movie.id, DAY)
    assert notifier.to(1) == ["That movie is no longer available."]
    async with env.session_factory() as session:
        assert (await session.scalar(text("select count(*) from jobs"))) == 0


async def test_the_booking_buttons_say_booking_is_coming(env: Env, notifier: FakeNotifier) -> None:
    await search.handle_book(env, _sender(1), 5)
    assert notifier.to(1) == ["Booking arrives in a later update."]


# --- the worker side ----------------------------------------------------------------------------


async def test_one_screen_type_goes_straight_to_the_times(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.showtimes = [showtime("1", 13), showtime("2", 16), showtime("3", 19)]

    result = await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))

    assert result == {"showtimes": 3}
    assert notifier.to(1) == ["Tap a showtime to book 2 seats:"]
    labels = [label for label, _ in notifier.buttons_to(1)]
    assert labels == ["1:00 PM", "4:00 PM", "7:00 PM"]  # Phoenix time, not UTC
    assert all(data.startswith("book:") for _, data in notifier.buttons_to(1))
    assert chain.showtime_calls == [("moana", DAY)]


async def test_several_screen_types_ask_which_first_and_the_tap_shows_that_formats_times(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.showtimes = [
        showtime("1", 13, "Digital"),
        showtime("2", 16, "IMAX"),
        showtime("3", 19, "Digital"),
    ]

    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))

    assert notifier.to(1) == ["Pick a screen type:"]
    assert notifier.buttons_to(1) == [
        ("Digital", f"fmt:{movie.id}:2026-11-17:{keyboards.format_code('Digital')}"),
        ("IMAX", f"fmt:{movie.id}:2026-11-17:{keyboards.format_code('IMAX')}"),
    ]

    notifier.sent.clear()
    bot_env = Env(worker_env.session_factory, notifier, 1000, clock=lambda: LOOKUP_TIME)
    await search.handle_format(bot_env, _sender(1), movie.id, DAY, keyboards.format_code("IMAX"))
    assert [label for label, _ in notifier.buttons_to(1)] == ["4:00 PM"]
    notifier.sent.clear()
    await search.handle_format(bot_env, _sender(1), movie.id, DAY, keyboards.format_code("Digital"))
    assert [label for label, _ in notifier.buttons_to(1)] == ["1:00 PM", "7:00 PM"]


async def test_a_format_that_is_not_offered_asks_for_the_date_again(
    env: Env, notifier: FakeNotifier
) -> None:
    await approved_user(env.session_factory, 1)
    movie = await _movie(env)
    await search.handle_format(env, _sender(1), movie.id, DAY, "deadbeef")
    await search.handle_format(env, _sender(1), 999_999, DAY, "deadbeef")
    assert notifier.to(1) == [
        "Those showtimes changed. Pick the date again.",
        "That movie is no longer available.",
    ]


async def test_a_day_without_showtimes_says_so_naming_the_theatre(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.showtimes = []
    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))
    assert notifier.to(1) == ["No showtimes at Arizona Mills that day."]
    assert notifier.buttons_to(1) == []


async def test_a_session_the_chain_stopped_returning_is_not_offered(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.showtimes = [showtime("1", 13), showtime("2", 16)]
    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))
    notifier.sent.clear()

    chain.showtimes = [showtime("2", 16)]  # the 1:00 PM show was cancelled
    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))

    assert [label for label, _ in notifier.buttons_to(1)] == ["4:00 PM"]


async def test_the_lookup_is_skipped_for_someone_no_longer_approved(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1, status="denied")
    movie = await _movie(worker_env)
    chain.showtimes = [showtime("1", 13)]
    result = await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))
    assert result == {"skipped": True}
    assert notifier.sent == []
    assert chain.showtime_calls == []  # and the site was not visited


async def test_the_lookup_is_skipped_for_a_movie_that_is_gone(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    job = await _job(worker_env, user.id, movie)
    async with worker_env.session_factory() as session, session.begin():
        await session.execute(text("delete from movies"))
    assert await search_showtimes.run(worker_env, job) == {"skipped": True}
    assert notifier.sent == []


async def test_a_cancelled_or_started_session_never_shows_after_a_format_tap(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    """The format list the user tapped and the times shown must come from the same sessions."""
    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.showtimes = [
        showtime("1", 13, "Digital"),
        showtime("2", 16, "IMAX"),
        showtime("3", 19, "Digital"),
    ]
    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))

    # Session 1 is cancelled: the chain stops returning it, but its old row stays in the table.
    chain.showtimes = [showtime("2", 16, "IMAX"), showtime("3", 19, "Digital")]
    await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))
    async with worker_env.session_factory() as session, session.begin():
        await session.execute(
            text(
                "update showtimes set updated_at = updated_at - interval '2 hours' where external_session_id = '1'"
            )
        )
    notifier.sent.clear()

    bot_env = Env(worker_env.session_factory, notifier, 1000, clock=lambda: LOOKUP_TIME)
    await search.handle_format(bot_env, _sender(1), movie.id, DAY, keyboards.format_code("Digital"))
    assert [label for label, _ in notifier.buttons_to(1)] == [
        "7:00 PM"
    ]  # not the cancelled 1:00 PM

    # Once the 7 PM show has started, it is not offered either.
    notifier.sent.clear()
    late = Env(
        worker_env.session_factory,
        notifier,
        1000,
        clock=lambda: datetime(2026, 11, 18, 3, 30, tzinfo=UTC),
    )
    await search.handle_format(late, _sender(1), movie.id, DAY, keyboards.format_code("Digital"))
    assert notifier.to(1) == ["Those showtimes changed. Pick the date again."]


async def test_the_same_date_tapped_twice_queues_one_lookup(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1)
    movie = await _movie(env)
    await search.handle_date(clocked, _sender(1), movie.id, DAY)
    await search.handle_date(clocked, _sender(1), movie.id, DAY)
    assert notifier.to(1) == ["Looking up showtimes…", "Already looking that up…"]
    async with env.session_factory() as session:
        assert (await session.scalar(text("select count(*) from jobs"))) == 1


async def test_a_user_cannot_pile_up_lookups(env: Env, notifier: FakeNotifier) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1)
    movie = await _movie(env)
    for day in range(16, 21):  # five different dates
        await search.handle_date(clocked, _sender(1), movie.id, date(2026, 11, day))
    assert notifier.to(1)[:3] == ["Looking up showtimes…"] * 3
    assert notifier.to(1)[3:] == ["Still looking things up. Try again in a moment."] * 2
    async with env.session_factory() as session:
        assert (await session.scalar(text("select count(*) from jobs"))) == search.MAX_OPEN_SEARCHES


async def test_another_users_lookups_do_not_count_against_you(
    env: Env, notifier: FakeNotifier
) -> None:
    clocked = replace(env, clock=lambda: CLOCK)
    await approved_user(env.session_factory, 1)
    await approved_user(env.session_factory, 2)
    movie = await _movie(env)
    for day in range(16, 19):
        await search.handle_date(clocked, _sender(1), movie.id, date(2026, 11, day))
    await search.handle_date(clocked, _sender(2), movie.id, DAY)
    assert notifier.to(2) == ["Looking up showtimes…"]


async def test_a_lookup_for_a_user_who_changed_city_is_skipped(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 1, city="blr")  # moved to Bangalore
    movie = await _movie(worker_env)  # a Phoenix movie
    chain.showtimes = [showtime("1", 13)]
    assert await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie)) == {
        "skipped": True
    }
    assert chain.showtime_calls == []


async def test_a_busy_theatre_site_tells_the_user_instead_of_retrying_forever(
    worker_env: WorkerEnv, chain: FakeChain, notifier: FakeNotifier
) -> None:
    from showwatch.chains.base import BlockedError

    user = await approved_user(worker_env.session_factory, 1)
    movie = await _movie(worker_env)
    chain.error = BlockedError("backing off")
    result = await search_showtimes.run(worker_env, await _job(worker_env, user.id, movie))
    assert result == {"blocked": True}
    assert notifier.to(1) == ["The theatre site is busy right now. Try again in a few minutes."]
