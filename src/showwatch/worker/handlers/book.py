"""book: hold seats for one user on one showtime and tell them the outcome.

Outcomes (stored in `jobs.result`): `held` (seats and the payment link), `manual` (the seat-map
link, for the user to choose seats), `failed` (anything went wrong). A job that fails here still
completes: it has one attempt, and the user has already been told.
"""

from datetime import UTC, datetime
from typing import Any

import structlog

from showwatch.chains.base import BlockedError, RetryLater
from showwatch.chains.harkins.booker import NUM_SEATS
from showwatch.db.repositories import catalog, movies, users
from showwatch.domain.models import Capability, Job
from showwatch.worker.handlers import WorkerEnv

log = structlog.get_logger(__name__)

HELD = "Seats {seats} held! Pay here: {url}"
MANUAL = "Couldn't auto-pick seats; choose {count} yourself here: {url}"
FAILED = "Booking failed — try again."
BUSY = "The theatre site is busy right now. Try again in a few minutes."
STARTED = "That showtime has already started."


async def run(env: WorkerEnv, job: Job) -> dict[str, Any]:
    user_id, showtime_id = job.payload["user_id"], job.payload["showtime_id"]
    seats = job.payload.get("seats", NUM_SEATS)

    async with env.session_factory() as session:
        user = await users.get(session, user_id)
        showtime = await movies.get_showtime(session, showtime_id)
        movie = await movies.get(session, showtime.movie_id) if showtime else None
        chain_info = await catalog.get_chain(session, movie.chain_id) if movie else None
    if (
        user is None
        or user.status != "approved"
        or showtime is None
        or movie is None
        or chain_info is None
    ):
        return {"outcome": "failed", "reason": "gone"}
    chat_id = user.chat_id or user.telegram_user_id

    if seats != NUM_SEATS:
        await env.notifier.send(chat_id, f"Only {NUM_SEATS} seats can be booked for now.")
        return {"outcome": "failed", "reason": "seat count"}
    if showtime.starts_at <= datetime.now(UTC):
        await env.notifier.send(chat_id, STARTED)
        return {"outcome": "failed", "reason": "started"}

    try:
        chain = env.registry.require(chain_info.code, Capability.BOOK)
        result = await chain.book(showtime, seats)
    except RetryLater:
        raise  # the runner puts the job back without counting an attempt
    except BlockedError:
        await env.notifier.send(chat_id, BUSY)
        return {"outcome": "failed", "reason": "blocked"}
    except Exception as error:  # a booking never crashes the worker or leaves the user waiting
        log.exception("book_failed", job_id=job.id)
        await env.notifier.send(chat_id, FAILED)
        return {"outcome": "failed", "reason": type(error).__name__}

    if result.kind == "held":
        await env.notifier.send(
            chat_id, HELD.format(seats=" + ".join(result.seats), url=result.url)
        )
        return {"outcome": "held", "seats": result.seats, "url": result.url}
    await env.notifier.send(chat_id, MANUAL.format(count=seats, url=result.url))
    return {"outcome": "manual", "url": result.url}
