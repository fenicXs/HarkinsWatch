"""The Postgres job queue against a real database."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.db import queue
from showwatch.db.repositories import catalog

SessionFactory = async_sessionmaker[AsyncSession]

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


async def _ids(session: AsyncSession) -> dict[str, int]:
    harkins = await catalog.get_chain_by_code(session, "harkins")
    bms = await catalog.get_chain_by_code(session, "bms")
    phx = await catalog.get_city_by_code(session, "phx")
    blr = await catalog.get_city_by_code(session, "blr")
    assert harkins and bms and phx and blr
    return {"harkins": harkins.id, "bms": bms.id, "phx": phx.id, "blr": blr.id}


async def test_claim_returns_nothing_when_the_queue_is_empty(session: AsyncSession) -> None:
    assert await queue.claim(session, "w1") is None


async def test_claim_respects_run_after(session: AsyncSession) -> None:
    await queue.enqueue(session, "search_showtimes", {"n": 1}, run_after=T0 + timedelta(hours=1))
    assert await queue.claim(session, "w1", now=T0) is None
    job = await queue.claim(session, "w1", now=T0 + timedelta(hours=2))
    assert job is not None
    assert job.status == "running"
    assert job.locked_by == "w1"


async def test_claim_takes_the_oldest_runnable_job_first(session: AsyncSession) -> None:
    await queue.enqueue(
        session, "search_showtimes", {"n": "later"}, run_after=T0 - timedelta(minutes=1)
    )
    await queue.enqueue(
        session, "search_showtimes", {"n": "oldest"}, run_after=T0 - timedelta(hours=1)
    )
    job = await queue.claim(session, "w1", now=T0)
    assert job is not None
    assert job.payload == {"n": "oldest"}


async def test_two_workers_never_claim_the_same_job(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        for n in range(50):
            await queue.enqueue(session, "search_showtimes", {"n": n}, run_after=T0)
        await session.commit()

    async def worker(name: str) -> list[int]:
        claimed: list[int] = []
        while True:
            async with session_factory() as session:
                job = await queue.claim(session, name, now=T0 + timedelta(hours=1))
                await session.commit()
            if job is None:
                return claimed
            claimed.append(job.id)

    first, second = await asyncio.gather(worker("w1"), worker("w2"))
    everything = first + second
    assert len(everything) == 50
    assert len(set(everything)) == 50  # no job claimed twice
    assert first and second  # both workers took part


async def test_a_running_book_job_blocks_another_for_the_same_chain_only(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        ids = await _ids(session)
        first = await queue.enqueue(
            session, "book", {"n": 1}, chain_id=ids["harkins"], run_after=T0
        )
        second = await queue.enqueue(
            session, "book", {"n": 2}, chain_id=ids["harkins"], run_after=T0
        )
        other_chain = await queue.enqueue(
            session, "book", {"n": 3}, chain_id=ids["bms"], run_after=T0
        )
        await session.commit()
    now = T0 + timedelta(hours=1)

    async with session_factory() as session:
        claimed = await queue.claim(session, "w1", now=now)
        await session.commit()
    assert claimed is not None
    assert claimed.id == first.id

    async with session_factory() as session:
        claimed = await queue.claim(session, "w2", now=now)
        await session.commit()
    assert claimed is not None
    assert claimed.id == other_chain.id  # harkins is busy, so the bms job runs instead

    async with session_factory() as session:
        assert await queue.claim(session, "w3", now=now) is None  # only the blocked one is left
        await queue.complete(session, first.id)
        await session.commit()
        later = await queue.claim(session, "w3", now=now)
    assert later is not None
    assert later.id == second.id


async def test_check_chain_is_limited_per_chain_and_city(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        ids = await _ids(session)
        phx1 = await queue.enqueue(
            session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
        )
        await queue.enqueue(
            session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
        )
        blr = await queue.enqueue(
            session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["blr"], run_after=T0
        )
        await session.commit()
    now = T0 + timedelta(hours=1)
    claimed_ids = []
    for worker in ("w1", "w2", "w3"):
        async with session_factory() as session:
            job = await queue.claim(session, worker, now=now)
            await session.commit()
        if job:
            claimed_ids.append(job.id)
    assert claimed_ids == [phx1.id, blr.id]  # the second harkins+phx job waits


async def test_the_database_refuses_two_running_book_jobs_for_one_chain(
    session: AsyncSession,
) -> None:
    ids = await _ids(session)
    await queue.enqueue(session, "book", {}, chain_id=ids["harkins"])
    await queue.enqueue(session, "book", {}, chain_id=ids["harkins"])
    with pytest.raises(IntegrityError):
        await session.execute(text("update jobs set status = 'running'"))


async def test_claim_survives_a_race_between_two_workers_on_one_chain(
    session_factory: SessionFactory,
) -> None:
    """Worker B passes the skip check before A commits; the unique index stops B, and B backs off."""
    async with session_factory() as session:
        ids = await _ids(session)
        first = await queue.enqueue(
            session, "book", {"n": 1}, chain_id=ids["harkins"], run_after=T0
        )
        await queue.enqueue(session, "book", {"n": 2}, chain_id=ids["harkins"], run_after=T0)
        await session.commit()
    now = T0 + timedelta(hours=1)

    async with session_factory() as a, session_factory() as b:
        # Record database errors on B's connection, to prove the unique index is what stopped it.
        errors: list[str] = []
        assert b.bind is not None
        event.listen(
            b.bind.sync_engine,  # type: ignore[attr-defined]
            "handle_error",
            lambda ctx: errors.append(str(ctx.original_exception)),
        )
        claimed_by_a = await queue.claim(a, "A", now=now)  # running, not yet committed
        assert claimed_by_a is not None
        assert claimed_by_a.id == first.id
        task_b = asyncio.create_task(queue.claim(b, "B", now=now))
        await asyncio.sleep(0.5)  # B is now waiting on A's uncommitted row
        assert not task_b.done()
        await a.commit()
        assert await asyncio.wait_for(task_b, timeout=10) is None

    assert any("uq_jobs_running_limit" in error for error in errors)


async def test_complete_stores_the_result_and_clears_the_lock(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)
    claimed = await queue.claim(session, "w1", now=T0)
    assert claimed is not None
    done = await queue.complete(session, job.id, {"kind": "held", "seats": ["G6", "G7"]})
    assert done.status == "done"
    assert done.result == {"kind": "held", "seats": ["G6", "G7"]}
    assert done.locked_by is None
    assert done.locked_at is None


async def test_fail_retries_with_growing_back_off_then_gives_up(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(
        session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
    )
    assert job.max_attempts == 3

    first = await queue.fail(session, job.id, "boom 1", now=T0)
    assert (first.status, first.attempts, first.run_after) == (
        "queued",
        1,
        T0 + timedelta(seconds=60),
    )
    second = await queue.fail(session, job.id, "boom 2", now=T0)
    assert (second.status, second.attempts, second.run_after) == (
        "queued",
        2,
        T0 + timedelta(seconds=120),
    )
    third = await queue.fail(session, job.id, "boom 3", now=T0)
    assert (third.status, third.attempts, third.last_error) == ("failed", 3, "boom 3")


async def test_a_book_job_is_never_retried(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"])
    failed = await queue.fail(session, job.id, "browser crashed", now=T0)
    assert failed.status == "failed"
    assert failed.attempts == 1


async def test_requeue_does_not_count_an_attempt(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)
    await queue.claim(session, "w1", now=T0)
    back = await queue.requeue(session, job.id, 60, now=T0)
    assert (back.status, back.attempts, back.run_after) == ("queued", 0, T0 + timedelta(seconds=60))
    assert back.locked_by is None


async def test_sweep_recovers_jobs_from_dead_workers(session: AsyncSession) -> None:
    ids = await _ids(session)
    crashed_check = await queue.enqueue(
        session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
    )
    crashed_book = await queue.enqueue(session, "book", {}, chain_id=ids["bms"], run_after=T0)
    fresh = await queue.enqueue(session, "search_showtimes", {}, run_after=T0)
    for _ in range(3):
        await queue.claim(session, "dead-worker", now=T0)  # all three are now running, locked at T0

    # The search job is claimed again later, so its lock is recent.
    await session.execute(
        text("update jobs set locked_at = :at where id = :id"),
        {"at": T0 + timedelta(minutes=14), "id": fresh.id},
    )
    swept = await queue.sweep(session, lock_timeout_s=900, now=T0 + timedelta(minutes=16))

    by_id = {job.id: job for job in swept}
    assert set(by_id) == {crashed_check.id, crashed_book.id}  # the fresh job is left alone
    assert (by_id[crashed_check.id].status, by_id[crashed_check.id].attempts) == ("queued", 1)
    assert by_id[crashed_book.id].status == "failed"
    assert by_id[crashed_book.id].last_error == "worker crashed"


async def test_finishing_a_job_that_does_not_exist_is_an_error(session: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await queue.complete(session, 999_999)
    with pytest.raises(LookupError):
        await queue.fail(session, 999_999, "boom")
    with pytest.raises(LookupError):
        await queue.requeue(session, 999_999, 60)
