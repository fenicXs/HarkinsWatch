"""Plain-language requests: "Dune IMAX Friday evening", in any language.

Two steps, both answered by the bot at once: a message is parsed by the LLM and shown back with a
Confirm button (stored for an hour as a `parsed_requests` row); the tap does the work.
What the work is depends on what the movie's chain can do (docs/plan.md, M6):
- a chain that lists sessions (Harkins): one `search_showtimes` job per day, filtered by the
  part of day and format asked for, then the usual book buttons;
- a watch-only chain (BookMyShow, District): a watch, so the user is alerted with a link when
  tickets open;
- a title nothing lists yet: a watch for the title, as `/watch` does.
"""

from collections import deque
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender, search, watch
from showwatch.chains.capabilities import can_search_showtimes
from showwatch.db import queue
from showwatch.db.repositories import catalog, movies, requests, users, watches
from showwatch.domain.models import ChainInfo, Movie, ParsedRequest, User
from showwatch.domain.title_match import match_for_watch
from showwatch.llm.client import LLMClient
from showwatch.llm.tasks import intent, title_match

REQUEST_LIFETIME = timedelta(hours=1)
MAX_TEXTS_PER_MINUTE = 6
DAYS_WITHOUT_DATE = 3  # today and the next two, when only a time of day or format was given
SLOW_DOWN = "Slow down a little: try again in a minute."
NOT_CAUGHT = "I didn't catch that. Try /watch <movie title>."
EXPIRED = "That request expired. Send it again."
OUT_OF_RANGE = "Showtimes are only listed for the next 7 days. Ask again for a day this week."
STILL_LOOKING = "Still looking things up. Try again in a moment."
MAX_OPEN_LOOKUPS = 7  # one request can ask for a week; more open lookups than that wait
CITY_CHANGED = "You changed city since then. Send your request again."


async def _resolve(llm: LLMClient, title: str, listed: list[Movie]) -> Movie | None:
    """The listed movie the user means: a clear title match first, else the model picks."""
    titles = [m.title for m in listed]
    chosen = match_for_watch(title, titles)
    if chosen is None:
        picks = await title_match.match_titles(llm, title, titles)
        chosen = picks[0] if picks else None
    return next((m for m in listed if m.title == chosen), None) if chosen else None


def _can_search(movie: Movie | None, chain: ChainInfo | None) -> bool:
    """Showtimes exist to search only for a movie that is on sale at a chain that lists them."""
    return (
        movie is not None
        and chain is not None
        and movie.status == "now_showing"
        and can_search_showtimes(chain.code)
    )


def _days(parsed: dict[str, Any]) -> list[date]:
    first, last = parsed.get("date_from"), parsed.get("date_to")
    if not first:
        return []
    start = date.fromisoformat(first)
    end = date.fromisoformat(last or first)
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


CITY_NAMES = {
    "phx": ("phoenix", "phx"),
    "blr": ("bengaluru", "bangalore", "bengalooru", "blr"),
}


def _named_other_city(said: str | None, current_code: str) -> str | None:
    """The code of a different known city the message names, else None (ignored or unknown)."""
    if not said:
        return None
    word = said.strip().casefold()
    for code, names in CITY_NAMES.items():
        if word in names and code != current_code:
            return code
    return None


def _allow(env: Env, telegram_user_id: int) -> bool:
    """At most MAX_TEXTS_PER_MINUTE model calls per person: each message costs LLM calls."""
    now = env.clock()
    recent = env.recent_texts.setdefault(telegram_user_id, deque())
    while recent and now - recent[0] > timedelta(minutes=1):
        recent.popleft()
    if len(recent) >= MAX_TEXTS_PER_MINUTE:
        return False
    recent.append(now)
    return True


def _describe(parsed: ParsedRequest, movie: Movie | None, action: str, *, searching: bool) -> str:
    bits = [movie.title if movie else parsed.title]
    if not searching:
        # A watch is not tied to a day or a time of day: do not read those back as if it were.
        return f"{bits[0]}.\n{action}"
    days = _days(
        {
            "date_from": parsed.date_from.isoformat() if parsed.date_from else None,
            "date_to": parsed.date_to.isoformat() if parsed.date_to else None,
        }
    )
    if days:
        bits.append(
            days[0].strftime("%a %b %d")
            if len(days) == 1
            else f"{days[0].strftime('%a %b %d')} to {days[-1].strftime('%a %b %d')}"
        )
    if parsed.part_of_day:
        bits.append(parsed.part_of_day)
    if parsed.format:
        bits.append(parsed.format)
    note = " Booking holds 2 seats for now." if parsed.seats != search.NUM_SEATS else ""
    return f"{', '.join(bits)}.\n{action}{note}"


async def handle_text(env: Env, sender: Sender, text: str, help_text: str) -> None:
    """A message that is not a command: parse it, show what was understood, ask to confirm."""
    llm = env.llm
    if llm is None or not llm.configured:
        await env.notifier.send(sender.chat_id, help_text)
        return
    async with env.session_factory() as session:
        user = await users.get_by_telegram_id(session, sender.telegram_user_id)
        if user is None or user.status != "approved":
            return
        if user.current_city_id is None:
            await env.notifier.send(sender.chat_id, watch.NEED_CITY)
            return
        city = await catalog.get_city(session, user.current_city_id)
        assert city is not None
        listed = await movies.list_for_city(session, city.id, current_only=True)

    if not _allow(env, sender.telegram_user_id):
        await env.notifier.send(sender.chat_id, SLOW_DOWN)
        return
    today = env.clock().astimezone(ZoneInfo(city.tz)).date()
    parsed = await intent.parse_request(llm, text, today=today, city_name=city.name)
    if parsed is None:
        await env.notifier.send(sender.chat_id, NOT_CAUGHT)
        return
    other = _named_other_city(parsed.city, city.code)
    if other is not None:
        await env.notifier.send(
            sender.chat_id,
            f"That looks like a request for another city. I'm searching {city.name}: "
            "switch with /city first, then send it again.",
        )
        return
    movie = await _resolve(llm, parsed.title, listed)

    async with env.session_factory() as session, session.begin():
        await requests.purge_expired(session, env.clock())  # keeps the table small
        chain = await catalog.get_chain(session, movie.chain_id) if movie else None
        searching = _can_search(movie, chain)
        if movie is None:
            action = "It isn't listed yet. I'll keep watching and tell you when it appears."
        elif searching:
            action = "I'll look up showtimes and show you what's available."
        else:
            action = "I'll watch it and message you with a link the moment tickets open."
        payload: dict[str, Any] = {
            "title": parsed.title,
            "movie_id": movie.id if movie else None,
            "city_id": city.id,
            "date_from": parsed.date_from.isoformat() if parsed.date_from else None,
            "date_to": parsed.date_to.isoformat() if parsed.date_to else None,
            "part_of_day": parsed.part_of_day,
            "format": parsed.format,
        }
        request_id = await requests.create(
            session, user.id, payload, env.clock() + REQUEST_LIFETIME
        )
    await env.notifier.send(
        sender.chat_id,
        _describe(parsed, movie, action, searching=searching),
        keyboards.confirm_intent(request_id),
    )


async def handle_intent(env: Env, sender: Sender, request_id: int) -> None:
    """The Confirm tap: do what was shown."""
    dates_for: tuple[Movie, str] | None = None
    async with env.session_factory() as session, session.begin():
        user = await users.get_by_telegram_id(session, sender.telegram_user_id)
        if user is None or user.status != "approved":
            return
        payload = await requests.take(session, user.id, request_id, env.clock())
        if payload is None:
            text = EXPIRED
        elif payload["city_id"] != user.current_city_id:
            text = CITY_CHANGED
        else:
            text, dates_for = await _act(env, session, user, payload)
    if dates_for is not None:
        await search.send_dates(env, sender.chat_id, dates_for[0], dates_for[1])
        return
    await env.notifier.send(sender.chat_id, text)


async def _act(
    env: Env, session: Any, user: User, payload: dict[str, Any]
) -> tuple[str, tuple[Movie, str] | None]:
    movie = await movies.get(session, payload["movie_id"]) if payload["movie_id"] else None
    if movie is None or movie.city_id != user.current_city_id:
        return await watch.watch_for_title(session, user, payload["title"]), None
    chain = await catalog.get_chain(session, movie.chain_id)
    if not _can_search(movie, chain):
        _, created = await watches.add(
            session,
            user_id=user.id,
            city_id=movie.city_id,
            query=movie.title,
            movie_id=movie.id,
        )
        done = f"Watching {movie.title}." if created else f"You're already watching {movie.title}."
        return f"{done} I'll message you with a link the moment tickets open.", None

    city = await catalog.get_city(session, movie.city_id)
    assert city is not None
    today = env.clock().astimezone(ZoneInfo(city.tz)).date()
    days = [d for d in _days(payload) if today <= d < today + timedelta(days=search.DAYS_OFFERED)]
    if not days and _days(payload):
        return OUT_OF_RANGE, None  # they named days, but none is inside the week we can see
    if not days and (payload.get("part_of_day") or payload.get("format")):
        # No date but a time of day or screen type: look at the next few days with those
        # filters, rather than a date picker that would forget them.
        days = [today + timedelta(days=n) for n in range(DAYS_WITHOUT_DATE)]
    if not days:
        return "", (movie, city.tz)  # no usable date: let them pick one
    open_jobs = await queue.open_payloads_for_user(session, "search_showtimes", user.id)
    room = MAX_OPEN_LOOKUPS - len(open_jobs)
    if room <= 0:
        return STILL_LOOKING, None
    days = days[:room]
    for day in days:
        job: dict[str, Any] = {"user_id": user.id, "movie_id": movie.id, "day": day.isoformat()}
        if payload.get("part_of_day"):
            job["part_of_day"] = payload["part_of_day"]
        if payload.get("format"):
            job["format"] = payload["format"]
        if job in open_jobs:
            continue
        await queue.enqueue(session, "search_showtimes", job, chain_id=movie.chain_id)
    word = "day" if len(days) == 1 else "days"
    return f"Looking up showtimes for {len(days)} {word}…", None
