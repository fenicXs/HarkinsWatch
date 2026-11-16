"""Finding a showtime: movie -> date -> (screen type) -> time. Booking itself arrives with M4.

Every button carries record ids. Looking up showtimes is slow, so the date tap only queues a
`search_showtimes` job; the worker answers with the format or time buttons. Tapping a format
needs no job: the sessions are already stored, so it reads them from the database.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender
from showwatch.db import queue
from showwatch.db.repositories import catalog, movies, users
from showwatch.domain.models import Movie, Showtime, User
from showwatch.notify import Buttons

DAYS_OFFERED = 7
NO_SHOWTIMES = "No showtimes at {theatre} that day."


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """Midnight to midnight of a local day, as UTC datetimes."""
    zone = ZoneInfo(tz_name)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def time_label(starts_at: datetime, tz_name: str) -> str:
    """'4:20 PM' in the theatre's local time."""
    return starts_at.astimezone(ZoneInfo(tz_name)).strftime("%I:%M %p").lstrip("0")


def formats_of(showtimes: Sequence[Showtime]) -> list[str]:
    return sorted({s.format for s in showtimes})


def choices_for_day(
    movie: Movie, day: date, showtimes: Sequence[Showtime], tz_name: str, theatre_name: str
) -> tuple[str, Buttons | None]:
    """What to show after a lookup: nothing found, a screen-type choice, or the times."""
    if not showtimes:
        return NO_SHOWTIMES.format(theatre=theatre_name), None
    formats = formats_of(showtimes)
    if len(formats) > 1:
        return "Pick a screen type:", keyboards.format_choices(movie.id, day, formats)
    return "Tap a showtime to book 2 seats:", keyboards.time_choices(
        [(s.id, time_label(s.starts_at, tz_name)) for s in showtimes]
    )


def _today(env: Env, tz_name: str) -> date:
    return env.clock().astimezone(ZoneInfo(tz_name)).date()


async def send_dates(env: Env, chat_id: int, movie: Movie, tz_name: str) -> None:
    """The next 7 days as buttons, starting from today in the theatre's time zone."""
    today = _today(env, tz_name)
    days = [today + timedelta(days=n) for n in range(DAYS_OFFERED)]
    await env.notifier.send(
        chat_id, f"{movie.title}: pick a date.", keyboards.date_choices(movie.id, days)
    )


async def _context(
    session: AsyncSession, sender: Sender, movie_id: int
) -> tuple[User, Movie, str] | None:
    """The approved user, a movie in their city, and that city's time zone; else None."""
    user = await users.get_by_telegram_id(session, sender.telegram_user_id)
    if user is None or user.status != "approved" or user.current_city_id is None:
        return None
    movie = await movies.get(session, movie_id)
    city = await catalog.get_city(session, user.current_city_id)
    if movie is None or city is None or movie.city_id != city.id:
        return None
    return user, movie, city.tz


async def handle_pick(env: Env, sender: Sender, movie_id: int) -> None:
    """The "Book seats" button: offer dates."""
    async with env.session_factory() as session:
        found = await _context(session, sender, movie_id)
    if found is None:
        await env.notifier.send(sender.chat_id, "That movie is no longer available.")
        return
    _, movie, tz_name = found
    await send_dates(env, sender.chat_id, movie, tz_name)


async def handle_date(env: Env, sender: Sender, movie_id: int, day: date) -> None:
    """A date was tapped: queue the lookup (the worker sends the answer)."""
    async with env.session_factory() as session, session.begin():
        found = await _context(session, sender, movie_id)
        if found is None:
            text = "That movie is no longer available."
        else:
            user, movie, tz_name = found
            today = _today(env, tz_name)
            if not today <= day < today + timedelta(days=DAYS_OFFERED):
                text = "That date is out of range. Tap a date from the list."
            else:
                await queue.enqueue(
                    session,
                    "search_showtimes",
                    {"user_id": user.id, "movie_id": movie.id, "day": day.isoformat()},
                    chain_id=movie.chain_id,
                )
                text = "Looking up showtimes…"
    await env.notifier.send(sender.chat_id, text)


async def handle_format(env: Env, sender: Sender, movie_id: int, day: date, index: int) -> None:
    """A screen type was tapped: show that format's times from the stored sessions."""
    async with env.session_factory() as session:
        found = await _context(session, sender, movie_id)
        if found is None:
            text, buttons = "That movie is no longer available.", None
        else:
            user, movie, tz_name = found
            assert user.current_city_id is not None
            theatres = await catalog.list_theatres(session, movie.chain_id, user.current_city_id)
            start, end = day_bounds(day, tz_name)
            stored = [
                s
                for theatre in theatres
                for s in await movies.list_showtimes(
                    session, movie.id, theatre.id, starts_from=start, starts_before=end
                )
            ]
            formats = formats_of(stored)
            if not 0 <= index < len(formats):
                text, buttons = "Those showtimes changed. Pick the date again.", None
            else:
                chosen = [s for s in stored if s.format == formats[index]]
                text = "Tap a showtime to book 2 seats:"
                buttons = keyboards.time_choices(
                    [(s.id, time_label(s.starts_at, tz_name)) for s in chosen]
                )
    await env.notifier.send(sender.chat_id, text, buttons)


async def handle_book(env: Env, sender: Sender, showtime_id: int) -> None:
    """A showtime was tapped. Booking arrives in a later update (M4)."""
    await env.notifier.send(sender.chat_id, "Booking arrives in a later update.")
