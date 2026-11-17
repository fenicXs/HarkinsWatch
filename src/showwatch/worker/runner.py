"""The job runner: claim a job, run its handler, record the outcome."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.chains.base import BlockedError, RetryLater
from showwatch.db import queue
from showwatch.db.queue import JobNotOwnedError
from showwatch.domain.models import Job
from showwatch.worker.handlers import (
    WorkerEnv,
    book,
    check_chain,
    probe_signals,
    search_showtimes,
)

Handler = Callable[[WorkerEnv, Job], Awaitable[dict[str, Any]]]

HANDLERS: dict[str, Handler] = {
    "book": book.run,
    "check_chain": check_chain.run,
    "probe_signals": probe_signals.run,
    "search_showtimes": search_showtimes.run,
}

IDLE_POLL_SECONDS = 2
BLOCKED_RETRY_SECONDS = 300  # a chain that pushed back is left alone for at least this long

log = structlog.get_logger(__name__)


async def _record(env: WorkerEnv, action: Callable[[AsyncSession], Awaitable[Job]]) -> None:
    """Write a job's outcome. If the job was swept and given to someone else, leave it alone."""
    try:
        async with env.session_factory() as session, session.begin():
            await action(session)
    except JobNotOwnedError as error:
        log.warning("job_not_owned", reason=str(error))


async def run_one(env: WorkerEnv, worker_id: str) -> bool:
    """Run one job if there is one. Returns False when the queue had nothing runnable."""
    async with env.session_factory() as session, session.begin():
        job = await queue.claim(session, worker_id)
    if job is None:
        return False

    try:
        handler = HANDLERS.get(job.kind)
        if handler is None:
            raise LookupError(f"no handler for job kind {job.kind!r}")
        result = await handler(env, job)
    except BlockedError as error:
        # The site pushed back. Not our failure and not an attempt: try again after the back-off.
        log.warning("job_blocked", job_id=job.id, kind=job.kind, reason=str(error))
        await _record(env, lambda s: queue.requeue(s, job.id, worker_id, BLOCKED_RETRY_SECONDS))
    except RetryLater as error:
        log.warning("job_retry_later", job_id=job.id, kind=job.kind, reason=str(error))
        delay_s = error.delay_s
        await _record(env, lambda s: queue.requeue(s, job.id, worker_id, delay_s))
    except Exception as error:
        log.exception("job_failed", job_id=job.id, kind=job.kind)
        message = f"{type(error).__name__}: {error}"
        await _record(env, lambda s: queue.fail(s, job.id, worker_id, message))
    else:
        await _record(env, lambda s: queue.complete(s, job.id, worker_id, result))
    return True


async def run_forever(env: WorkerEnv, worker_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            worked = await run_one(env, worker_id)
        except Exception:
            log.exception("runner_error")
            worked = False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_POLL_SECONDS)
            except TimeoutError:
                pass
