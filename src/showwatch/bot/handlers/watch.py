"""/watch, /list, /unwatch and /city. Every action is scoped to the sender."""

from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender, search
from showwatch.db.repositories import catalog, movies, users, watches
from showwatch.domain.models import Movie, User, WatchItem
from showwatch.domain.title_match import find_matches

MAX_QUERY_LENGTH = 80
MAX_LISTED = 30  # keeps /list under Telegram's 4096-character limit

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
            listed = await movies.list_for_city(session, user.current_city_id, current_only=True)
            titles = find_matches(query, [m.title for m in listed])
            chosen = _movies_with_titles(listed, titles)
            if chosen:
                rows = list(keyboards.movie_choices(chosen))
                none_of_these = keyboards.watch_for(query)
                if none_of_these is not None:  # close spellings are not always the right film
                    rows.append([none_of_these])
                text, buttons = "Which one?", rows
            else:
                # Nothing listed matches yet: keep watching for the title (insider mode).
                text = await _watch_for_title(session, user, query)
                buttons = None
    await env.notifier.send(sender.chat_id, text, buttons)


async def _watch_for_title(session: AsyncSession, user: User, query: str) -> str:
    """Save a watch for a title nothing lists yet. Same words in any case count as one watch."""
    assert user.current_city_id is not None
    existing = await watches.list_for_user(session, user.id)
    if any(w.query.casefold() == query.casefold() for w in existing):
        return f"You're already watching for '{query}'."
    await watches.add(session, user_id=user.id, city_id=user.current_city_id, query=query)
    return (
        f"No listed movie matches '{query}' yet. I'll keep watching for it and tell you when "
        "something with that title appears. /list shows your watches and /unwatch removes one."
    )


async def handle_watch_for(env: Env, sender: Sender, query: str) -> None:
    """The user tapped "None of these": watch for exactly what they typed."""
    query = " ".join(query.split())
    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        if user is None:
            return
        if not query or len(query) > MAX_QUERY_LENGTH or user.current_city_id is None:
            text = NEED_CITY if user.current_city_id is None else "Please use a shorter title."
        else:
            text = await _watch_for_title(session, user, query)
    await env.notifier.send(sender.chat_id, text)


def _movies_with_titles(listed: list[Movie], titles: list[str]) -> list[Movie]:
    """The movies behind the matched titles (several movies can share a title), at most 5."""
    chosen: list[Movie] = []
    for title in titles:
        for movie in listed:
            if movie.title == title and movie not in chosen:
                chosen.append(movie)
    return chosen[:5]


async def handle_watch_pick(env: Env, sender: Sender, movie_id: int) -> None:
    offer_dates: tuple[Movie, str] | None = None
    async with env.session_factory() as session, session.begin():
        user = await _user(session, sender)
        if user is None:
            return
        movie = await movies.get(session, movie_id)
        if user.current_city_id is None:
            text = NEED_CITY
        elif movie is None or movie.city_id != user.current_city_id:
            text = "That movie is no longer available."
        elif movie.status == "now_showing":
            # Saves nothing: an already-playing movie goes straight to the date picker.
            city = await catalog.get_city(session, user.current_city_id)
            assert city is not None
            offer_dates = (movie, city.tz)
            text = ""
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
    if offer_dates is not None:
        await search.send_dates(env, sender.chat_id, offer_dates[0], offer_dates[1])
        return
    await env.notifier.send(sender.chat_id, text)


# --- /list and /unwatch -----------------------------------------------------------------------


async def handle_list(env: Env, sender: Sender) -> None:
    async with env.session_factory() as session:
        user = await _user(session, sender)
        if user is None:
            return
        items = await watches.list_for_user(session, user.id)
        lines = [
            f"{n}. {await _label(session, item)}: {STAGE_TEXT.get(item.stage, item.stage)}"
            for n, item in enumerate(items[:MAX_LISTED], start=1)
        ]
        if len(items) > MAX_LISTED:
            lines.append(f"...and {len(items) - MAX_LISTED} more.")
    text = "Your watches:\n" + "\n".join(lines) if lines else "You're not watching anything."
    await env.notifier.send(sender.chat_id, text)


async def handle_unwatch(env: Env, sender: Sender) -> None:
    async with env.session_factory() as session:
        user = await _user(session, sender)
        if user is None:
            return
        items = await watches.list_for_user(session, user.id)
        choices = [(item.id, await _label(session, item)) for item in items[:MAX_LISTED]]
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
