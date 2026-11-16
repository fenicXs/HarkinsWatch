"""The scheduler: which checks get queued, when, and what happens to dead jobs."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from showwatch.db import queue
from showwatch.db.repositories import catalog
from showwatch.worker.handlers import WorkerEnv
from showwatch.worker.scheduler import jittered, tick
from telegramfakes import FakeNotifier
from workerfakes import approved_user

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


# --- jitter (no database) -----------------------------------------------------------------------


@pytest.mark.parametrize(("roll", "expected"), [(0.0, 1440.0), (0.5, 1800.0), (1.0, 2160.0)])
def test_jitter_stays_within_twenty_percent_of_the_interval(roll: float, expected: float) -> None:
    assert jittered(1800, 20, lambda: roll) == pytest.approx(expected)


def test_jitter_is_zero_when_switched_off() -> None:
    assert jittered(1800, 0, lambda: 0.9) == 1800


def test_a_thousand_random_jitters_never_leave_the_band() -> None:
    values = [jittered(1800, 20) for _ in range(1000)]
    assert min(values) >= 1440
    assert max(values) <= 2160
    assert len(set(values)) > 900  # and it really varies


# --- the tick -----------------------------------------------------------------------------------


async def _open_jobs(env: WorkerEnv) -> list[tuple[str, dict, datetime]]:  # type: ignore[type-arg]
    async with env.session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "select kind, payload, run_after from jobs where status = 'queued' order by id"
                )
            )
        ).all()
    return [(r.kind, r.payload, r.run_after) for r in rows]


async def test_the_first_tick_queues_a_check_for_each_chain_and_city_that_has_a_theatre(
    worker_env: WorkerEnv,
) -> None:
    result = await tick(worker_env, now=NOW)
    # harkins is enabled and registered and has a theatre in Phoenix only; bms is disabled and
    # unregistered; Bangalore has no Harkins theatre.
    assert result.enqueued == [("harkins", "phx")]
    ((kind, payload, run_after),) = await _open_jobs(worker_env)
    assert (kind, payload) == ("check_chain", {"chain": "harkins", "city": "phx"})
    assert run_after == NOW  # never checked before: due now


async def test_a_check_that_is_already_queued_is_not_queued_again(worker_env: WorkerEnv) -> None:
    await tick(worker_env, now=NOW)
    again = await tick(worker_env, now=NOW + timedelta(minutes=1))
    assert again.enqueued == []
    assert len(await _open_jobs(worker_env)) == 1


async def test_after_a_check_finishes_the_next_is_due_one_interval_later_with_jitter(
    worker_env: WorkerEnv,
) -> None:
    await tick(worker_env, now=NOW)
    async with worker_env.session_factory() as session, session.begin():
        job = await queue.claim(session, "w1", now=NOW)
        assert job is not None
        await queue.complete(session, job.id, "w1", {})
        await session.execute(text("update jobs set updated_at = :at"), {"at": NOW})

    for roll, minutes in [(0.0, 24), (0.5, 30), (1.0, 36)]:  # 1800 s, plus or minus 20%
        async with worker_env.session_factory() as session, session.begin():
            await session.execute(text("delete from jobs where status = 'queued'"))
        result = await tick(worker_env, now=NOW + timedelta(minutes=1), rng=lambda r=roll: r)
        assert result.enqueued == [("harkins", "phx")]
        (_, _, run_after) = (await _open_jobs(worker_env))[0]
        assert run_after == NOW + timedelta(minutes=minutes)


async def test_an_overdue_check_is_queued_to_run_right_away(worker_env: WorkerEnv) -> None:
    await tick(worker_env, now=NOW)
    async with worker_env.session_factory() as session, session.begin():
        job = await queue.claim(session, "w1", now=NOW)
        assert job is not None
        await queue.complete(session, job.id, "w1", {})
        await session.execute(text("update jobs set updated_at = :at"), {"at": NOW})
    later = NOW + timedelta(hours=5)  # the worker was down for hours
    await tick(worker_env, now=later)
    ((_, _, run_after),) = await _open_jobs(worker_env)
    assert run_after == later  # not scheduled in the past, and not delayed further


async def test_a_dead_workers_job_is_recovered_by_the_tick(worker_env: WorkerEnv) -> None:
    await tick(worker_env, now=NOW)
    async with worker_env.session_factory() as session, session.begin():
        await queue.claim(session, "dead-worker", now=NOW)

    result = await tick(worker_env, now=NOW + timedelta(minutes=20))  # past the lock timeout

    assert result.swept == 1
    assert result.enqueued == []  # the recovered job is queued again, so no second one is added


async def test_a_lost_booking_is_reported_to_the_user(
    worker_env: WorkerEnv, notifier: FakeNotifier
) -> None:
    user = await approved_user(worker_env.session_factory, 77)
    async with worker_env.session_factory() as session, session.begin():
        harkins = await catalog.get_chain_by_code(session, "harkins")
        assert harkins
        await queue.enqueue(
            session,
            "book",
            {"user_id": user.id, "showtime_id": 1, "seats": 2},
            chain_id=harkins.id,
            run_after=NOW,
        )
        await queue.claim(session, "dead-worker", now=NOW)

    await tick(worker_env, now=NOW + timedelta(minutes=20))

    assert notifier.to(77) == ["Booking failed — try again."]
