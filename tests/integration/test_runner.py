"""The job runner: claim, run the handler, record the outcome."""

from typing import Any

import pytest
from sqlalchemy import text

from showwatch.chains.base import BlockedError
from showwatch.db import queue
from showwatch.db.repositories import catalog
from showwatch.domain.models import Job
from showwatch.worker import runner
from showwatch.worker.handlers import WorkerEnv
from workerfakes import FakeChain


async def _status(env: WorkerEnv, job_id: int) -> dict[str, Any]:
    async with env.session_factory() as session:
        row = (
            await session.execute(
                text(
                    "select status, attempts, last_error, result, locked_by, "
                    "run_after > now() as later from jobs where id = :id"
                ),
                {"id": job_id},
            )
        ).one()
    return dict(row._mapping)


async def _enqueue(env: WorkerEnv, kind: str = "search_showtimes", **kwargs: Any) -> Job:
    async with env.session_factory() as session, session.begin():
        return await queue.enqueue(session, kind, {"x": 1}, **kwargs)


@pytest.fixture
def handlers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    table: dict[str, Any] = {}
    monkeypatch.setattr(runner, "HANDLERS", table)
    return table


async def test_an_empty_queue_has_nothing_to_run(worker_env: WorkerEnv) -> None:
    assert await runner.run_one(worker_env, "w1") is False


async def test_a_successful_job_is_completed_with_its_result(
    worker_env: WorkerEnv, handlers: dict[str, Any]
) -> None:
    async def handler(env: WorkerEnv, job: Job) -> dict[str, Any]:
        return {"seen": job.payload["x"]}

    handlers["search_showtimes"] = handler
    job = await _enqueue(worker_env)

    assert await runner.run_one(worker_env, "w1") is True

    state = await _status(worker_env, job.id)
    assert (state["status"], state["result"], state["locked_by"]) == ("done", {"seen": 1}, None)


async def test_a_crashing_handler_fails_the_job_and_keeps_the_error(
    worker_env: WorkerEnv, handlers: dict[str, Any]
) -> None:
    async def handler(env: WorkerEnv, job: Job) -> dict[str, Any]:
        raise RuntimeError("boom")

    handlers["search_showtimes"] = handler
    job = await _enqueue(worker_env)

    await runner.run_one(worker_env, "w1")

    state = await _status(worker_env, job.id)
    assert (state["status"], state["attempts"]) == ("queued", 1)  # search jobs get one retry
    assert state["last_error"] == "RuntimeError: boom"
    assert state["later"] is True  # after the back-off, not at once


async def test_a_job_with_no_handler_fails(worker_env: WorkerEnv, handlers: dict[str, Any]) -> None:
    async with worker_env.session_factory() as session, session.begin():
        harkins = await catalog.get_chain_by_code(session, "harkins")
        assert harkins
        job = await queue.enqueue(session, "book", {}, chain_id=harkins.id)
    await runner.run_one(worker_env, "w1")
    state = await _status(worker_env, job.id)
    assert (state["status"], "no handler" in state["last_error"]) == ("failed", True)


async def test_a_site_that_pushes_back_requeues_the_job_without_counting_an_attempt(
    worker_env: WorkerEnv, handlers: dict[str, Any]
) -> None:
    async def handler(env: WorkerEnv, job: Job) -> dict[str, Any]:
        raise BlockedError("harkins.com stopped us")

    handlers["search_showtimes"] = handler
    job = await _enqueue(worker_env)

    await runner.run_one(worker_env, "w1")

    state = await _status(worker_env, job.id)
    assert (state["status"], state["attempts"], state["later"]) == ("queued", 0, True)


async def test_a_worker_that_lost_its_job_to_a_sweep_does_not_overwrite_it(
    worker_env: WorkerEnv, handlers: dict[str, Any]
) -> None:
    async def slow_handler(env: WorkerEnv, job: Job) -> dict[str, Any]:
        async with env.session_factory() as session, session.begin():  # someone else took the job
            await session.execute(text("update jobs set locked_by = 'someone-else'"))
        return {"late": True}

    handlers["search_showtimes"] = slow_handler
    job = await _enqueue(worker_env)

    assert await runner.run_one(worker_env, "w1") is True  # no exception

    state = await _status(worker_env, job.id)
    assert (state["status"], state["locked_by"], state["result"]) == (
        "running",
        "someone-else",
        None,
    )


async def test_the_real_handlers_are_registered() -> None:
    assert set(runner.HANDLERS) == {"check_chain", "search_showtimes", "book"}


async def test_a_real_check_chain_job_runs_end_to_end(
    worker_env: WorkerEnv, chain: FakeChain
) -> None:
    chain.movies = [
        *[
            __import__("workerfakes").listing(f"film-{n}")  # a small, valid Now Showing list
            for n in range(6)
        ]
    ]
    async with worker_env.session_factory() as session, session.begin():
        harkins = await catalog.get_chain_by_code(session, "harkins")
        phx = await catalog.get_city_by_code(session, "phx")
        assert harkins and phx
        job = await queue.enqueue(
            session,
            "check_chain",
            {"chain": "harkins", "city": "phx"},
            chain_id=harkins.id,
            city_id=phx.id,
        )

    await runner.run_one(worker_env, "w1")

    state = await _status(worker_env, job.id)
    assert state["status"] == "done"
    assert state["result"]["movies"] == 6
    assert state["result"]["first_run"] is True
