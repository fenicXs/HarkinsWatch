"""The Postgres job queue (docs/plan.md Appendix B).

Callers own the transaction: `claim` flushes and the caller commits, so other workers see the
job as `running`. Concurrency limits are checked in two places: `claim` skips a queued job whose
(kind, chain[, city]) is already running, and the unique index `uq_jobs_running_limit` makes the
database refuse a second running job if two workers race past that check.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from showwatch.db import tables
from showwatch.db.tables import LIMITED_JOB_KINDS
from showwatch.domain.models import Job

Limit = Literal["chain_city", "chain"] | None

MAX_CLAIM_TRIES = 20
RUNNING_LIMIT_INDEX = "uq_jobs_running_limit"


@dataclass(frozen=True)
class JobSpec:
    max_attempts: int
    backoff_base_s: int | None  # None: never retried
    limit: Limit  # which running jobs block another one of the same kind


JOB_SPECS: dict[str, JobSpec] = {
    "check_chain": JobSpec(max_attempts=3, backoff_base_s=60, limit="chain_city"),
    "search_showtimes": JobSpec(max_attempts=2, backoff_base_s=30, limit=None),
    "book": JobSpec(max_attempts=1, backoff_base_s=None, limit="chain"),
    "probe_signals": JobSpec(max_attempts=3, backoff_base_s=60, limit="chain"),
}


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(spec: JobSpec, attempts: int) -> int | None:
    """base × 2^(attempts − 1), where `attempts` counts failed runs including this one."""
    if spec.backoff_base_s is None:
        return None
    return spec.backoff_base_s * (1 << (attempts - 1))


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    chain_id: int | None = None,
    city_id: int | None = None,
    run_after: datetime | None = None,
) -> Job:
    """Add a job. `max_attempts` comes from the catalogue; limited kinds need their chain (and city)."""
    spec = JOB_SPECS.get(kind)
    if spec is None:
        raise ValueError(f"unknown job kind: {kind}")
    if spec.limit == "chain_city" and (chain_id is None or city_id is None):
        raise ValueError(f"{kind} needs chain_id and city_id")
    if spec.limit == "chain" and (chain_id is None or city_id is not None):
        raise ValueError(f"{kind} needs chain_id and no city_id")
    row = tables.Job(
        kind=kind,
        payload=payload,
        max_attempts=spec.max_attempts,
        chain_id=chain_id,
        city_id=city_id,
    )
    if run_after is not None:
        row.run_after = run_after
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return Job.model_validate(row)


async def has_open_job(
    session: AsyncSession, kind: str, *, chain_id: int | None, city_id: int | None
) -> bool:
    """Is a job of this kind for this chain and city already queued or running?"""
    stmt = select(
        exists().where(
            tables.Job.kind == kind,
            tables.Job.status.in_(("queued", "running")),
            tables.Job.chain_id.is_(None) if chain_id is None else tables.Job.chain_id == chain_id,
            tables.Job.city_id.is_(None) if city_id is None else tables.Job.city_id == city_id,
        )
    )
    return bool(await session.scalar(stmt))


async def last_finished_at(
    session: AsyncSession, kind: str, *, chain_id: int, city_id: int
) -> datetime | None:
    """When the most recent job of this kind for this chain and city finished (done or failed)."""
    return await session.scalar(
        select(func.max(tables.Job.updated_at)).where(
            tables.Job.kind == kind,
            tables.Job.status.in_(("done", "failed")),
            tables.Job.chain_id == chain_id,
            tables.Job.city_id == city_id,
        )
    )


async def claim(
    session: AsyncSession, worker_id: str, *, now: datetime | None = None
) -> Job | None:
    """Take the oldest runnable job, or return None. The caller commits to publish the claim."""
    now = now or _now()
    running = aliased(tables.Job)
    blocked = exists().where(
        running.status == "running",
        running.kind == tables.Job.kind,
        tables.Job.kind.in_(LIMITED_JOB_KINDS),
        running.chain_id == tables.Job.chain_id,
        or_(tables.Job.kind != "check_chain", running.city_id == tables.Job.city_id),
    )
    skip: list[int] = []
    for _ in range(MAX_CLAIM_TRIES):
        candidate_id: int | None = None
        stmt = (
            select(tables.Job)
            .where(tables.Job.status == "queued", tables.Job.run_after <= now, ~blocked)
            .order_by(tables.Job.run_after, tables.Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if skip:
            stmt = stmt.where(tables.Job.id.not_in(skip))
        try:
            async with session.begin_nested():
                row = await session.scalar(stmt)
                if row is None:
                    return None
                candidate_id = row.id
                row.status = "running"
                row.locked_by = worker_id
                row.locked_at = now
                await session.flush()
                await session.refresh(row)
                return Job.model_validate(row)
        except IntegrityError as error:
            # Only the running-limit index means "another worker started a job for the same
            # chain between our check and our update". Anything else is a real error.
            if RUNNING_LIMIT_INDEX not in str(error.orig) or candidate_id is None:
                raise
            skip.append(candidate_id)
    return None


class JobNotOwnedError(Exception):
    """The job is no longer running under this worker: it was swept and re-queued, or finished."""


async def _owned(session: AsyncSession, job_id: int, worker_id: str) -> tables.Job:
    """Load a job for update, and make sure this worker still holds it.

    A worker that outlives the lock timeout can be swept and its job claimed again. Without
    this check, the slow worker would later overwrite the new run's state.
    """
    row = await session.get(tables.Job, job_id, with_for_update=True)
    if row is None:
        raise LookupError(f"job {job_id} not found")
    if row.status != "running" or row.locked_by != worker_id:
        raise JobNotOwnedError(f"job {job_id} is not running under worker {worker_id}")
    return row


async def complete(
    session: AsyncSession, job_id: int, worker_id: str, result: dict[str, Any] | None = None
) -> Job:
    row = await _owned(session, job_id, worker_id)
    row.status = "done"
    row.result = result
    row.locked_by = None
    row.locked_at = None
    await session.flush()
    await session.refresh(row)
    return Job.model_validate(row)


async def fail(
    session: AsyncSession,
    job_id: int,
    worker_id: str,
    error: str,
    *,
    now: datetime | None = None,
) -> Job:
    """Record a failed run: retry with back-off, or give up once `max_attempts` is reached."""
    now = now or _now()
    row = await _owned(session, job_id, worker_id)
    row.attempts += 1
    row.last_error = error
    row.locked_by = None
    row.locked_at = None
    delay = backoff_seconds(JOB_SPECS[row.kind], row.attempts)
    if row.attempts >= row.max_attempts or delay is None:
        row.status = "failed"
    else:
        row.status = "queued"
        row.run_after = now + timedelta(seconds=delay)
    await session.flush()
    await session.refresh(row)
    return Job.model_validate(row)


async def requeue(
    session: AsyncSession,
    job_id: int,
    worker_id: str,
    delay_s: int,
    *,
    now: datetime | None = None,
) -> Job:
    """Put a job back without counting an attempt (for example, a locked browser profile)."""
    now = now or _now()
    row = await _owned(session, job_id, worker_id)
    row.status = "queued"
    row.run_after = now + timedelta(seconds=delay_s)
    row.locked_by = None
    row.locked_at = None
    await session.flush()
    await session.refresh(row)
    return Job.model_validate(row)


async def sweep(
    session: AsyncSession, lock_timeout_s: int, *, now: datetime | None = None
) -> list[Job]:
    """Recover jobs whose worker died: `running` for longer than the lock timeout.

    Each swept job counts as a failed attempt: it goes back to `queued` (after the usual back-off), or to `failed` with
    `last_error = "worker crashed"` once it has no attempts left (a `book` job has one).
    Returns the swept jobs in their new state.
    """
    now = now or _now()
    rows = await session.scalars(
        select(tables.Job)
        .where(
            tables.Job.status == "running",
            or_(
                tables.Job.locked_at.is_(None),
                tables.Job.locked_at < now - timedelta(seconds=lock_timeout_s),
            ),
        )
        .order_by(tables.Job.id)
        .with_for_update(skip_locked=True)
    )
    swept: list[Job] = []
    for row in rows.all():
        row.attempts += 1
        row.last_error = "worker crashed"
        row.locked_by = None
        row.locked_at = None
        delay = backoff_seconds(JOB_SPECS[row.kind], row.attempts)
        if row.attempts >= row.max_attempts or delay is None:
            row.status = "failed"
        else:
            row.status = "queued"
            row.run_after = now + timedelta(seconds=delay)  # no instant retry of a crash loop
        await session.flush()
        await session.refresh(row)
        swept.append(Job.model_validate(row))
    return swept
