"""Helpers for worker tests: a scriptable chain, listing builders and user/watch setup."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.chains.base import BaseChain
from showwatch.chains.politeness import Politeness
from showwatch.chains.registry import ChainRegistry
from showwatch.core.settings import Settings
from showwatch.db.repositories import catalog, users, watches
from showwatch.domain.models import (
    Capability,
    City,
    Movie,
    MovieListing,
    ShowtimeListing,
    Theatre,
    User,
)
from showwatch.worker.handlers import WorkerEnv
from telegramfakes import FakeNotifier


class FakeChain(BaseChain):
    """A chain whose answers the test sets. Records how it was called."""

    code = "harkins"
    capabilities = Capability.WATCH | Capability.SHOWTIMES

    def __init__(self) -> None:
        self.movies: list[MovieListing] = []
        self.showtimes: list[ShowtimeListing] = []
        self.error: Exception | None = None
        self.showtime_calls: list[tuple[str, date]] = []

    async def list_movies(self, city: City) -> list[MovieListing]:
        if self.error:
            raise self.error
        return list(self.movies)

    async def list_showtimes(
        self, movie: Movie, theatre: Theatre, day: date
    ) -> list[ShowtimeListing]:
        if self.error:
            raise self.error
        self.showtime_calls.append((movie.external_id, day))
        return list(self.showtimes)


def listing(slug: str, status: str = "now_showing", title: str | None = None) -> MovieListing:
    return MovieListing(
        slug,
        title or slug.replace("-", " ").title(),
        f"https://www.harkins.com/movies/{slug}",
        status,  # type: ignore[arg-type]
    )


def many(count: int, prefix: str = "film") -> list[MovieListing]:
    return [listing(f"{prefix}-{n:02d}") for n in range(count)]


def showtime(
    session_id: str, hour: int, fmt: str = "Standard", day: date = date(2026, 11, 17)
) -> ShowtimeListing:
    """A session at Arizona Mills (Phoenix time, UTC-7), starting at `hour` o'clock."""
    start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC) + timedelta(hours=7)
    return ShowtimeListing(
        session_id,
        "16",
        start,
        fmt,
        f"https://www.harkins.com/ticketing/theatre/16/session/{session_id}",
    )


def make_worker_env(
    session_factory: async_sessionmaker[AsyncSession], notifier: FakeNotifier, chain: FakeChain
) -> WorkerEnv:
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://unused")  # type: ignore[call-arg]
    registry = ChainRegistry()
    registry.register(chain)
    return WorkerEnv(
        session_factory=session_factory,
        notifier=notifier,
        registry=registry,
        politeness=Politeness(session_factory, per_minute=6000),
        settings=settings,
    )


async def approved_user(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
    *,
    city: str | None = "phx",
    status: str = "approved",
) -> User:
    async with session_factory() as session, session.begin():
        user, _ = await users.create_pending(
            session,
            telegram_user_id=telegram_user_id,
            chat_id=telegram_user_id,
            display_name=f"U{telegram_user_id}",
        )
        if status != "pending":
            user = await users.set_status(session, user.id, status)  # type: ignore[arg-type]
        if city:
            found = await catalog.get_city_by_code(session, city)
            assert found
            await users.set_current_city(session, user.id, found.id)
            user = await users.get(session, user.id)  # type: ignore[assignment]
        return user


async def add_watch(
    session_factory: async_sessionmaker[AsyncSession], user: User, query: str, *, city: str = "phx"
) -> int:
    async with session_factory() as session, session.begin():
        found = await catalog.get_city_by_code(session, city)
        assert found
        item, _ = await watches.add(session, user_id=user.id, city_id=found.id, query=query)
        return item.id
