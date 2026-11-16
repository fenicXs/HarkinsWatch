"""Helpers for tests that need a real Postgres: create databases, run migrations."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False  # keep pytest's logging as it is
    return cfg


def with_database_name(database_url: str, name: str) -> str:
    return make_url(database_url).set(database=name).render_as_string(hide_password=False)


async def recreate_database(database_url: str) -> None:
    """Drop and create the database named in the URL (connects to the `postgres` database)."""
    name = make_url(database_url).database
    assert name and name.startswith("showwatch"), f"refusing to recreate database {name!r}"
    admin = create_async_engine(
        with_database_name(database_url, "postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'drop database if exists "{name}" with (force)'))
            await connection.execute(text(f'create database "{name}"'))
    finally:
        await admin.dispose()


def migrate_sync(database_url: str, revision: str = "head") -> None:
    """Run `alembic upgrade` from synchronous code (no event loop running)."""
    command.upgrade(alembic_config(database_url), revision)


async def migrate(database_url: str, revision: str = "head") -> None:
    """Run `alembic upgrade` from async code: Alembic starts its own loop, so use a thread."""
    await asyncio.to_thread(migrate_sync, database_url, revision)


async def downgrade(database_url: str, revision: str = "base") -> None:
    await asyncio.to_thread(command.downgrade, alembic_config(database_url), revision)
