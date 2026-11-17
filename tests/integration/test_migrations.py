"""The first migration, run on an empty database of its own."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dbtools import downgrade, migrate, recreate_database, with_database_name
from showwatch.db.tables import Base

EXPECTED_TABLES = {
    "alerts",
    "chain_snapshots",
    "chains",
    "cities",
    "jobs",
    "movies",
    "parsed_requests",
    "showtimes",
    "signals",
    "theatres",
    "users",
    "watch_items",
}


@pytest.fixture
async def empty_database(database_url: str) -> str:
    url = with_database_name(database_url, "showwatch_migration_test")
    await recreate_database(url)
    return url


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    return names - {"alembic_version"}


async def test_migration_creates_the_tables_and_seed_rows(empty_database: str) -> None:
    await migrate(empty_database)
    engine = create_async_engine(empty_database)
    try:
        assert await _table_names(engine) == EXPECTED_TABLES
        async with engine.connect() as connection:
            cities = (
                await connection.execute(text("select code, tz from cities order by id"))
            ).all()
            chains = (
                await connection.execute(text("select code, enabled from chains order by id"))
            ).all()
            theatres = (
                await connection.execute(
                    text(
                        "select chains.code, cities.code, theatres.external_id, theatres.name "
                        "from theatres join chains on chains.id = theatres.chain_id "
                        "join cities on cities.id = theatres.city_id"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    assert [tuple(r) for r in cities] == [("phx", "America/Phoenix"), ("blr", "Asia/Kolkata")]
    assert [tuple(r) for r in chains] == [
        ("harkins", True),
        ("bms", True),
        ("district", True),
        ("pvrinox", False),
    ]
    assert sorted(tuple(r) for r in theatres) == [
        ("bms", "blr", "blr", "BookMyShow (all Bengaluru cinemas)"),
        ("district", "blr", "blr", "District (all Bengaluru cinemas)"),
        ("harkins", "phx", "16", "Arizona Mills"),
    ]


async def test_migration_can_be_undone_and_redone(empty_database: str) -> None:
    await migrate(empty_database)
    await downgrade(empty_database)
    engine = create_async_engine(empty_database)
    try:
        assert await _table_names(engine) == set()
    finally:
        await engine.dispose()
    await migrate(empty_database)  # and applies again from scratch


async def test_migration_matches_the_table_definitions(empty_database: str) -> None:
    """If tables.py changes without a new migration, Alembic sees a difference here."""
    await migrate(empty_database)
    engine = create_async_engine(empty_database)

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return list(compare_metadata(context, Base.metadata))

    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(diff)
    finally:
        await engine.dispose()
    assert differences == []
