"""search_showtimes: look up one movie's sessions for one day and send the user the choices."""

from datetime import date
from typing import Any

import structlog

from showwatch.bot.handlers import search
from showwatch.chains.base import BlockedError
from showwatch.db.repositories import catalog, movies, users
from showwatch.domain.day_parts import in_part_of_day
from showwatch.domain.models import Capability, Job, Showtime
from showwatch.worker.handlers import WorkerEnv

log = structlog.get_logger(__name__)
LOOKUP_FAILED = "Couldn't look up showtimes right now. Try again in a few minutes."


def _filtered(stored: list[Showtime], payload: dict[str, Any], tz_name: str) -> list[Showtime]:
    """Keep the sessions that fit the part of day and screen type asked for in words."""
    part, wanted = payload.get("part_of_day"), payload.get("format")
    shown = stored
    if part:
        shown = [s for s in shown if in_part_of_day(s.starts_at, part, tz_name)]
    if wanted:
        shown = [s for s in shown if str(wanted).casefold() in s.format.casefold()]
    return shown


async def run(env: WorkerEnv, job: Job) -> dict[str, Any]:
    user_id, movie_id = job.payload["user_id"], job.payload["movie_id"]
    day = date.fromisoformat(job.payload["day"])

    async with env.session_factory() as session:
        user = await users.get(session, user_id)
        movie = await movies.get(session, movie_id)
        city = await catalog.get_city(session, movie.city_id) if movie else None
        chain = await catalog.get_chain(session, movie.chain_id) if movie else None
        theatres = (
            await catalog.list_theatres(session, movie.chain_id, movie.city_id) if movie else []
        )
    if user is None or user.status != "approved" or movie is None or city is None or chain is None:
        return {"skipped": True}
    if user.current_city_id != movie.city_id:  # the user moved on since tapping the date
        return {"skipped": True}
    chat_id = user.chat_id or user.telegram_user_id
    if not theatres:
        await env.notifier.send(chat_id, "No theatre is set up for that movie yet.")
        return {"skipped": True}

    theatre = theatres[0]
    try:
        listings = await env.registry.require(chain.code, Capability.SHOWTIMES).list_showtimes(
            movie, theatre, day
        )
    except BlockedError:
        # The site asked us to slow down. Tell the user instead of retrying a button tap forever.
        await env.notifier.send(
            chat_id, "The theatre site is busy right now. Try again in a few minutes."
        )
        return {"blocked": True}
    except Exception as error:  # a lookup never ends without telling the person who asked
        log.exception("search_showtimes_failed", job_id=job.id)
        await env.notifier.send(chat_id, LOOKUP_FAILED)
        return {"failed": type(error).__name__}
    async with env.session_factory() as session, session.begin():
        await movies.upsert_showtimes(session, movie.id, theatre.id, listings)
        start, end = search.day_bounds(day, city.tz)
        current_ids = {s.external_session_id for s in listings}
        stored = [
            s
            for s in await movies.list_showtimes(
                session, movie.id, theatre.id, starts_from=start, starts_before=end
            )
            if s.external_session_id in current_ids  # only what the chain returned just now
        ]
    shown = _filtered(stored, job.payload, city.tz)
    if stored and not shown:
        await env.notifier.send(chat_id, "Nothing that day matches what you asked for.")
        return {"showtimes": 0, "filtered_out": len(stored)}
    text, buttons = search.choices_for_day(movie, day, shown, city.tz, theatre.name)
    await env.notifier.send(chat_id, text, buttons)
    return {"showtimes": len(shown)}
