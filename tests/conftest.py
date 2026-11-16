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
from showwatch.core.settings import Settings

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
