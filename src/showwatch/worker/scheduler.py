"""The scheduler: once a minute, make sure every chain and city is being checked.

Due times come from the `jobs` table (the last finished `check_chain` plus the interval), so
they survive restarts. Jitter (±`POLL_JITTER_PCT`) is applied to `run_after` so checks do not
land on a rigid beat. Early-signal polling (`probe_signals`) joins with the first chain that can
report signals (M5).
"""

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from showwatch.db import queue
from showwatch.db.repositories import catalog, users
from showwatch.domain.models import Job
from showwatch.worker.handlers import WorkerEnv

TICK_SECONDS = 60

log = structlog.get_logger(__name__)


def jittered(base_s: float, jitter_pct: int, rng: Callable[[], float] = random.random) -> float:
    """`base_s` plus or minus up to `jitter_pct` percent."""
    return base_s * (1 + (rng() * 2 - 1) * jitter_pct / 100)


@dataclass
class TickResult:
    enqueued: list[tuple[str, str]]  # (chain code, city code)
    swept: int


async def tick(
    env: WorkerEnv, *, now: datetime | None = None, rng: Callable[[], float] = random.random
) -> TickResult:
    """One scheduler pass: recover dead jobs, then queue any check that is not already queued."""
    now = now or datetime.now(UTC)
    settings = env.settings
    enqueued: list[tuple[str, str]] = []

    async with env.session_factory() as session, session.begin():
        swept = await queue.sweep(session, settings.job_lock_timeout_s, now=now)
    await _report_lost_bookings(env, swept)

    async with env.session_factory() as session, session.begin():
        cities = await catalog.list_cities(session)
        for chain in await catalog.list_enabled_chains(session):
            if chain.code not in env.registry.codes():
                continue
            for city in cities:
                if not await catalog.list_theatres(session, chain.id, city.id):
                    continue
                if await queue.has_open_job(
                    session, "check_chain", chain_id=chain.id, city_id=city.id
                ):
                    continue
                last = await queue.last_finished_at(
                    session, "check_chain", chain_id=chain.id, city_id=city.id
                )
                run_after = now
                if last is not None:
                    interval = jittered(settings.check_interval_s, settings.poll_jitter_pct, rng)
                    run_after = max(now, last + timedelta(seconds=interval))
                await queue.enqueue(
                    session,
                    "check_chain",
                    {"chain": chain.code, "city": city.code},
                    chain_id=chain.id,
                    city_id=city.id,
                    run_after=run_after,
                )
                enqueued.append((chain.code, city.code))
    return TickResult(enqueued=enqueued, swept=len(swept))


async def _report_lost_bookings(env: WorkerEnv, swept: list[Job]) -> None:
    """A `book` job whose worker died is failed for good (booking is never retried): tell the user."""
    for job in swept:
        if job.kind != "book" or job.status != "failed":
            continue
        async with env.session_factory() as session:
            user = await users.get(session, job.payload.get("user_id", 0))
        if user is not None:
            await env.notifier.send(
                user.chat_id or user.telegram_user_id, "Booking failed — try again."
            )


async def run_scheduler(env: WorkerEnv, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            result = await tick(env)
            if result.enqueued or result.swept:
                log.info("scheduler_tick", enqueued=result.enqueued, swept=result.swept)
        except Exception:
            log.exception("scheduler_tick_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except TimeoutError:
            pass
