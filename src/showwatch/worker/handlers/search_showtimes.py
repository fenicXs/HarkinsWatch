"""search_showtimes: look up one movie's sessions for one day and send the user the choices."""

from datetime import date
from typing import Any

from showwatch.bot.handlers import search
from showwatch.db.repositories import catalog, movies, users
from showwatch.domain.models import Capability, Job
from showwatch.worker.handlers import WorkerEnv


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
    chat_id = user.chat_id or user.telegram_user_id
    if not theatres:
        await env.notifier.send(chat_id, "No theatre is set up for that movie yet.")
        return {"skipped": True}

    theatre = theatres[0]
    listings = await env.registry.require(chain.code, Capability.SHOWTIMES).list_showtimes(
        movie, theatre, day
    )
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
    text, buttons = search.choices_for_day(movie, day, stored, city.tz, theatre.name)
    await env.notifier.send(chat_id, text, buttons)
    return {"showtimes": len(stored)}
