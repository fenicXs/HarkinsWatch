"""/watch, /list, /unwatch and /city. Every action is scoped to the sender."""

from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender
from showwatch.db.repositories import catalog, movies, users, watches
from showwatch.domain.models import Movie, User, WatchItem
from showwatch.domain.title_match import find_matches

MAX_QUERY_LENGTH = 80

STAGE_TEXT = {
    "waiting": "waiting for it to be listed",
    "listed": "listed, no dates yet",
    "dates": "dates are out",
    "venues": "theatres are out",
    "sessions": "tickets are open",
    "done": "done",
}

NEED_CITY = "Choose your city first with /city."


async def _user(session: AsyncSession, sender: Sender) -> User | None:
    user = await users.get_by_telegram_id(session, sender.telegram_user_id)
    return user if user is not None and user.status == "approved" else None


async def _label(session: AsyncSession, item: WatchItem) -> str:
    if item.movie_id is not None:
        movie = await movies.get(session, item.movie_id)
        if movie is not None:
            return movie.title
    return item.query


# --- /city ------------------------------------------------------------------------------------


async def handle_city(env: Env, sender: Sender) -> None:
    async with env.session_factory() as session:
        cities = await catalog.list_cities(session)
    await env.notifier.send(sender.chat_id, "Which city?", keyboards.city_choices(cities))


async def handle_city_pick(env: Env, sender: Sender, code: str) -> None:
    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        city = await catalog.get_city_by_code(session, code)
        if user is None:
            return
        if city is None:
            text = "That city is not available."
        else:
            await users.set_current_city(session, user.id, city.id)
            text = f"City set to {city.name}."
    await env.notifier.send(sender.chat_id, text)


# --- /watch -----------------------------------------------------------------------------------


async def handle_watch(env: Env, sender: Sender, query: str) -> None:
    query = " ".join(query.split())
    if not query:
        await env.notifier.send(sender.chat_id, "Usage: /watch <movie title>")
        return
    if len(query) > MAX_QUERY_LENGTH:
        await env.notifier.send(sender.chat_id, "Please use a shorter title.")
        return

    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        if user is None:
            return
        if user.current_city_id is None:
            text, buttons = NEED_CITY, None
        else:
            listed = await movies.list_for_city(session, user.current_city_id)
            titles = find_matches(query, [m.title for m in listed])
            chosen = _movies_with_titles(listed, titles)
            if chosen:
                text, buttons = "Which one?", keyboards.movie_choices(chosen)
            else:
                # Nothing listed matches yet: keep watching for the title (insider mode).
                await watches.add(
                    session, user_id=user.id, city_id=user.current_city_id, query=query
                )
                text = (
                    f"No listed movie matches '{query}' yet. I'll keep watching for it and "
                    "tell you when something with that title appears. /list shows your "
                    "watches and /unwatch removes one."
                )
                buttons = None
    await env.notifier.send(sender.chat_id, text, buttons)


def _movies_with_titles(listed: list[Movie], titles: list[str]) -> list[Movie]:
    """The movies behind the matched titles (several movies can share a title), at most 5."""
    chosen: list[Movie] = []
    for title in titles:
        for movie in listed:
            if movie.title == title and movie not in chosen:
                chosen.append(movie)
    return chosen[:5]


async def handle_watch_pick(env: Env, sender: Sender, movie_id: int) -> None:
    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        if user is None:
            return
        movie = await movies.get(session, movie_id)
        if movie is None or movie.city_id != user.current_city_id:
            text = "That movie is no longer available."
        elif movie.status == "now_showing":
            # Saves nothing: the date and showtime flow arrives with M3.
            text = f"{movie.title} is already playing. Booking arrives in a later update."
        else:
            assert user.current_city_id is not None
            _, created = await watches.add(
                session,
                user_id=user.id,
                city_id=user.current_city_id,
                query=movie.title,
                movie_id=movie.id,
            )
            text = (
                f"Watching {movie.title}." if created else f"You're already watching {movie.title}."
            )
    await env.notifier.send(sender.chat_id, text)


# --- /list and /unwatch -----------------------------------------------------------------------


async def handle_list(env: Env, sender: Sender) -> None:
    async with env.session_factory() as session:
        user = await _user(session, sender)
        if user is None:
            return
        items = await watches.list_for_user(session, user.id)
        lines = [
            f"{n}. {await _label(session, item)}: {STAGE_TEXT[item.stage]}"
            for n, item in enumerate(items, start=1)
        ]
    text = "Your watches:\n" + "\n".join(lines) if lines else "You're not watching anything."
    await env.notifier.send(sender.chat_id, text)


async def handle_unwatch(env: Env, sender: Sender) -> None:
    async with env.session_factory() as session:
        user = await _user(session, sender)
        if user is None:
            return
        items = await watches.list_for_user(session, user.id)
        choices = [(item.id, await _label(session, item)) for item in items]
    if not choices:
        await env.notifier.send(sender.chat_id, "You're not watching anything.")
        return
    await env.notifier.send(
        sender.chat_id, "Stop watching which?", keyboards.watch_choices(choices)
    )


async def handle_unwatch_pick(env: Env, sender: Sender, watch_item_id: int) -> None:
    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        if user is None:
            return
        # Look the watch up among the user's own, so another user's id finds nothing.
        own = {item.id: item for item in await watches.list_for_user(session, user.id)}
        item = own.get(watch_item_id)
        if item is None:
            text = "That watch is already gone."
        else:
            label = await _label(session, item)
            await watches.delete_for_user(session, user.id, item.id)
            text = f"Removed {label}."
    await env.notifier.send(sender.chat_id, text)
