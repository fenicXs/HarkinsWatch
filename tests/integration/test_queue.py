"""The Postgres job queue against a real database."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.db import queue
from showwatch.db.queue import JobNotOwnedError
from showwatch.db.repositories import catalog

SessionFactory = async_sessionmaker[AsyncSession]

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)  # a "now" at which everything enqueued at T0 is runnable


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


async def test_two_workers_never_claim_the_same_job(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        for n in range(50):
            await queue.enqueue(session, "search_showtimes", {"n": n}, run_after=T0)
        await session.commit()

    async def worker(name: str) -> list[int]:
        claimed: list[int] = []
        while True:
            async with session_factory() as session:
                job = await queue.claim(session, name, now=LATER)
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

    async with session_factory() as session:
        claimed = await queue.claim(session, "w1", now=LATER)
        await session.commit()
    assert claimed is not None
    assert claimed.id == first.id

    async with session_factory() as session:
        claimed = await queue.claim(session, "w2", now=LATER)
        await session.commit()
    assert claimed is not None
    assert claimed.id == other_chain.id  # harkins is busy, so the bms job runs instead

    async with session_factory() as session:
        assert await queue.claim(session, "w3", now=LATER) is None  # only the blocked one is left
        await queue.complete(session, first.id, "w1")
        await session.commit()
        later = await queue.claim(session, "w3", now=LATER)
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
    claimed_ids = []
    for worker in ("w1", "w2", "w3"):
        async with session_factory() as session:
            job = await queue.claim(session, worker, now=LATER)
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

    async with session_factory() as a, session_factory() as b:
        # Record database errors on B's connection, to prove the unique index is what stopped it.
        errors: list[str] = []
        assert b.bind is not None
        event.listen(
            b.bind.sync_engine,  # type: ignore[attr-defined]
            "handle_error",
            lambda ctx: errors.append(str(ctx.original_exception)),
        )
        claimed_by_a = await queue.claim(a, "A", now=LATER)  # running, not yet committed
        assert claimed_by_a is not None
        assert claimed_by_a.id == first.id
        task_b = asyncio.create_task(queue.claim(b, "B", now=LATER))
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
    done = await queue.complete(session, job.id, "w1", {"kind": "held", "seats": ["G6", "G7"]})
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

    expected = [("queued", 1, 60), ("queued", 2, 120)]
    for n, (status, attempts, delay) in enumerate(expected, start=1):
        assert await queue.claim(session, "w1", now=LATER) is not None
        failed = await queue.fail(session, job.id, "w1", f"boom {n}", now=T0)
        assert (failed.status, failed.attempts) == (status, attempts)
        assert failed.run_after == T0 + timedelta(seconds=delay)
        assert failed.locked_by is None

    assert await queue.claim(session, "w1", now=LATER) is not None
    last = await queue.fail(session, job.id, "w1", "boom 3", now=T0)
    assert (last.status, last.attempts, last.last_error) == ("failed", 3, "boom 3")


async def test_a_book_job_is_never_retried(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)
    await queue.claim(session, "w1", now=LATER)
    failed = await queue.fail(session, job.id, "w1", "browser crashed", now=T0)
    assert failed.status == "failed"
    assert failed.attempts == 1


async def test_requeue_does_not_count_an_attempt(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)
    await queue.claim(session, "w1", now=T0)
    back = await queue.requeue(session, job.id, "w1", 60, now=T0)
    assert (back.status, back.attempts, back.run_after) == ("queued", 0, T0 + timedelta(seconds=60))
    assert back.locked_by is None


async def test_only_the_worker_holding_a_job_can_finish_it(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)

    with pytest.raises(JobNotOwnedError):  # still queued: nobody holds it
        await queue.complete(session, job.id, "w1")

    await queue.claim(session, "w1", now=T0)
    for call in (
        queue.complete(session, job.id, "w2"),
        queue.fail(session, job.id, "w2", "boom"),
        queue.requeue(session, job.id, "w2", 60),
    ):
        with pytest.raises(JobNotOwnedError):  # held by w1, not w2
            await call

    finished = await queue.complete(session, job.id, "w1")
    assert finished.attempts == 0  # the refused calls changed nothing


async def test_a_swept_worker_cannot_overwrite_the_job_it_lost(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(
        session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
    )
    await queue.claim(session, "slow", now=T0)
    swept = await queue.sweep(session, lock_timeout_s=900, now=T0 + timedelta(minutes=20))
    assert [j.id for j in swept] == [job.id]
    reclaimed = await queue.claim(session, "fast", now=T0 + timedelta(minutes=21))
    assert reclaimed is not None

    with pytest.raises(JobNotOwnedError):  # the slow worker finally finishes: too late
        await queue.complete(session, job.id, "slow", {"late": True})
    with pytest.raises(JobNotOwnedError):
        await queue.fail(session, job.id, "slow", "late failure")

    done = await queue.complete(session, job.id, "fast", {"ok": True})
    assert (done.result, done.attempts) == ({"ok": True}, 1)  # only the sweep counted an attempt


async def test_sweep_recovers_jobs_from_dead_workers(session: AsyncSession) -> None:
    ids = await _ids(session)
    crashed_check = await queue.enqueue(
        session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
    )
    crashed_book = await queue.enqueue(session, "book", {}, chain_id=ids["bms"], run_after=T0)
    fresh = await queue.enqueue(session, "search_showtimes", {}, run_after=T0)
    for _ in range(3):
        await queue.claim(session, "dead-worker", now=T0)  # all three are now running, locked at T0

    # The search job's lock is recent.
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
        await queue.complete(session, 999_999, "w1")
    with pytest.raises(LookupError):
        await queue.fail(session, 999_999, "w1", "boom")
    with pytest.raises(LookupError):
        await queue.requeue(session, 999_999, "w1", 60)


async def test_a_swept_job_waits_out_the_back_off_before_it_runs_again(
    session: AsyncSession,
) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(
        session, "check_chain", {}, chain_id=ids["harkins"], city_id=ids["phx"], run_after=T0
    )
    await queue.claim(session, "dead-worker", now=T0)
    now = T0 + timedelta(minutes=20)
    (swept,) = await queue.sweep(session, lock_timeout_s=900, now=now)
    assert swept.id == job.id
    assert swept.run_after == now + timedelta(seconds=60)  # first back-off step, not "now"
    assert await queue.claim(session, "w2", now=now) is None  # not runnable yet
    assert await queue.claim(session, "w2", now=now + timedelta(seconds=61)) is not None


async def test_sweep_also_recovers_a_running_job_with_no_lock_time(session: AsyncSession) -> None:
    ids = await _ids(session)
    job = await queue.enqueue(session, "book", {}, chain_id=ids["harkins"], run_after=T0)
    await queue.claim(session, "w1", now=T0)
    await session.execute(text("update jobs set locked_at = null"))  # e.g. a manual edit
    (swept,) = await queue.sweep(session, lock_timeout_s=900, now=T0 + timedelta(minutes=1))
    assert (swept.id, swept.status) == (job.id, "failed")  # frees the chain's slot


async def test_claim_does_not_hide_database_errors_that_are_not_the_race(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await queue.enqueue(session, "search_showtimes", {}, run_after=T0)

    async def broken_flush(*args: object, **kwargs: object) -> None:
        raise IntegrityError(
            "update jobs ...", {}, Exception("violates check constraint ck_jobs_status")
        )

    monkeypatch.setattr(session, "flush", broken_flush)
    with pytest.raises(IntegrityError, match="ck_jobs_status"):
        await queue.claim(session, "w1", now=LATER)
