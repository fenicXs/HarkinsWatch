"""The `book` job: outcomes, messages, and how the runner treats a locked profile."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from showwatch.chains.base import BaseChain, BlockedError, RetryLater
from showwatch.db import queue
from showwatch.db.repositories import catalog, movies
from showwatch.domain.models import BookingResult, Capability, CapabilityNotSupported, Job, Showtime
from showwatch.worker import runner
from showwatch.worker.handlers import WorkerEnv, book
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, approved_user, listing, make_worker_env


@pytest.fixture
def chain() -> FakeChain:
    return FakeChain()


@pytest.fixture
def worker_env(session_factory, notifier: FakeNotifier, chain: FakeChain) -> WorkerEnv:  # type: ignore[no-untyped-def]
    return make_worker_env(session_factory, notifier, chain)


async def _setup(env: WorkerEnv, *, starts_in: timedelta = timedelta(hours=3)) -> tuple[int, int]:
    """An approved user and a stored showtime; returns (user id, showtime id)."""
    user = await approved_user(env.session_factory, 1)
    async with env.session_factory() as session, session.begin():
        harkins = await catalog.get_chain_by_code(session, "harkins")
        phx = await catalog.get_city_by_code(session, "phx")
        assert harkins and phx
        movie, _ = await movies.upsert_movie(session, harkins.id, phx.id, listing("moana"))
        theatre = (await catalog.list_theatres(session, harkins.id, phx.id))[0]
        from showwatch.domain.models import ShowtimeListing

        starts = datetime.now(UTC) + starts_in
        await movies.upsert_showtimes(
            session,
            movie.id,
            theatre.id,
            [
                ShowtimeListing(
                    "s1", "16", starts, "Standard", "https://www.harkins.com/x/session/s1"
                )
            ],
        )
        stored = await movies.list_showtimes(session, movie.id, theatre.id)
    return user.id, stored[0].id


def _job(user_id: int, showtime_id: int, seats: int = 2) -> Job:
    return Job(
        id=1,
        kind="book",
        payload={"user_id": user_id, "showtime_id": showtime_id, "seats": seats},
        status="running",
        run_after=datetime.now(UTC),
        attempts=0,
        max_attempts=1,
        chain_id=None,
        city_id=None,
        locked_by="w1",
        locked_at=datetime.now(UTC),
        last_error=None,
        result=None,
    )


async def test_held_seats_are_sent_with_the_payment_link(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    user_id, showtime_id = await _setup(worker_env)
    result = await book.run(worker_env, _job(user_id, showtime_id))

    assert chain.book_calls == [(showtime_id, 2)]
    assert notifier.to(1) == ["Seats I5 + I6 held! Pay here: https://pay.test/x"]
    assert result == {"outcome": "held", "seats": ["I5", "I6"], "url": "https://pay.test/x"}


async def test_a_manual_result_sends_the_seat_map_link(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    chain.book_result = BookingResult(kind="manual", seats=[], url="https://seats.test/map")
    user_id, showtime_id = await _setup(worker_env)

    result = await book.run(worker_env, _job(user_id, showtime_id))

    assert notifier.to(1) == [
        "Couldn't auto-pick seats; choose 2 yourself here: https://seats.test/map"
    ]
    assert result == {"outcome": "manual", "url": "https://seats.test/map"}


async def test_any_error_becomes_a_failed_outcome_and_a_message(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    chain.book_error = RuntimeError("browser died")
    user_id, showtime_id = await _setup(worker_env)

    result = await book.run(worker_env, _job(user_id, showtime_id))

    assert notifier.to(1) == ["Booking failed — try again."]
    assert result == {"outcome": "failed", "reason": "RuntimeError"}


async def test_a_blocked_site_is_reported_as_busy(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    chain.book_error = BlockedError("backing off")
    user_id, showtime_id = await _setup(worker_env)

    result = await book.run(worker_env, _job(user_id, showtime_id))

    assert notifier.to(1) == ["The theatre site is busy right now. Try again in a few minutes."]
    assert result["outcome"] == "failed"


async def test_other_seat_counts_are_refused_without_touching_the_site(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    user_id, showtime_id = await _setup(worker_env)
    result = await book.run(worker_env, _job(user_id, showtime_id, seats=4))

    assert chain.book_calls == []
    assert notifier.to(1) == ["Only 2 seats can be booked for now."]
    assert result["outcome"] == "failed"


async def test_a_session_that_started_is_not_booked(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    user_id, showtime_id = await _setup(worker_env, starts_in=timedelta(minutes=-5))
    await book.run(worker_env, _job(user_id, showtime_id))

    assert chain.book_calls == []
    assert notifier.to(1) == ["That showtime has already started."]


async def test_a_user_no_longer_approved_or_a_gone_showtime_is_skipped_quietly(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    user_id, showtime_id = await _setup(worker_env)
    assert (await book.run(worker_env, _job(user_id, 99999)))["outcome"] == "failed"
    assert (await book.run(worker_env, _job(99999, showtime_id)))["outcome"] == "failed"
    assert chain.book_calls == [] and notifier.sent == []


async def test_a_chain_that_cannot_book_is_a_failed_outcome(
    worker_env: WorkerEnv, notifier: FakeNotifier, chain: FakeChain
) -> None:
    chain.capabilities = Capability.WATCH
    user_id, showtime_id = await _setup(worker_env)

    result = await book.run(worker_env, _job(user_id, showtime_id))

    assert result == {"outcome": "failed", "reason": "CapabilityNotSupported"}
    assert notifier.to(1) == ["Booking failed — try again."]


async def test_the_base_chain_refuses_to_book() -> None:
    class Bare(BaseChain):
        code = "bare"
        capabilities = Capability.WATCH

    showtime = Showtime(
        id=1,
        movie_id=1,
        theatre_id=1,
        external_session_id="s",
        starts_at=datetime.now(UTC),
        format="Standard",
        booking_url="https://x.test",
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(CapabilityNotSupported):
        await Bare().book(showtime, 2)


async def test_a_locked_profile_puts_the_job_back_without_using_the_attempt(
    worker_env: WorkerEnv, chain: FakeChain
) -> None:
    chain.book_error = RetryLater("browser profile is in use", delay_s=60)
    user_id, showtime_id = await _setup(worker_env)
    async with worker_env.session_factory() as session, session.begin():
        harkins = await catalog.get_chain_by_code(session, "harkins")
        assert harkins
        queued = await queue.enqueue(
            session,
            "book",
            {"user_id": user_id, "showtime_id": showtime_id, "seats": 2},
            chain_id=harkins.id,
        )

    assert await runner.run_one(worker_env, "w1")

    async with worker_env.session_factory() as session:
        row = (
            await session.execute(
                text("select status, attempts, run_after > now() as later from jobs where id = :i"),
                {"i": queued.id},
            )
        ).one()
    assert (row.status, row.attempts, row.later) == ("queued", 0, True)
