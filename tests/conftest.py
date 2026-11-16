"""Shared fixtures. Integration tests need TEST_DATABASE_URL (environment or .env) or are skipped."""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dbtools import migrate_sync, recreate_database
from showwatch.bot.handlers import Env
from showwatch.bot.handlers.access import ensure_admin
from showwatch.core.settings import Settings
from showwatch.worker.handlers import WorkerEnv
from telegramfakes import FakeNotifier
from workerfakes import FakeChain, make_worker_env

# Tables the tests write to. Reference tables (cities, chains, theatres) keep their seed rows.
MUTABLE_TABLES = "alerts, signals, watch_items, showtimes, movies, jobs, chain_snapshots, users"


def _test_database_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    try:
        return Settings().test_database_url  # type: ignore[call-arg]  # reads .env
    except ValidationError:
        return None


@pytest.fixture(scope="session")
def database_url() -> str:
    """A freshly created, fully migrated test database."""
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    asyncio.run(recreate_database(url))
    migrate_sync(url)
    return url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text(f"truncate {MUTABLE_TABLES} restart identity cascade"))
        await connection.execute(text("update chains set blocked_until = null"))
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


# --- bot handler tests -------------------------------------------------------------------------

ADMIN_ID = 1000  # Telegram user id of the admin in the handler tests


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
async def env(session_factory: async_sessionmaker[AsyncSession], notifier: FakeNotifier) -> Env:
    env = Env(session_factory=session_factory, notifier=notifier, admin_telegram_user_id=ADMIN_ID)
    await ensure_admin(env)
    return env


# --- worker tests ------------------------------------------------------------------------------


@pytest.fixture
def chain() -> FakeChain:
    return FakeChain()


@pytest.fixture
def worker_env(
    session_factory: async_sessionmaker[AsyncSession], notifier: FakeNotifier, chain: FakeChain
) -> WorkerEnv:
    return make_worker_env(session_factory, notifier, chain)
