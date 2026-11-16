"""Rules the database itself enforces: CHECK values and foreign-key behaviour.

`compare_metadata` (test_migrations) does not compare CHECK bodies, so these tests do.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db.repositories import alerts, catalog, movies, signals, users, watches
from showwatch.domain.models import MovieListing

BAD_ROWS = {
    "users.status": "insert into users (telegram_user_id, status) values (1, 'bogus')",
    "movies.status": (
        "insert into movies (chain_id, city_id, external_id, title, normalized_title, url, status) "
        "select c.id, ci.id, 'x', 'x', 'x', 'u', 'bogus' from chains c, cities ci "
        "where c.code = 'harkins' and ci.code = 'phx'"
    ),
    "watch_items.stage": (
        "insert into watch_items (user_id, city_id, query, stage) "
        "select u.id, ci.id, 'q', 'bogus' from users u, cities ci "
        "where u.telegram_user_id = 7 and ci.code = 'phx'"
    ),
    "signals.stage": (
        "insert into signals (watch_item_id, stage, evidence) "
        "select id, 'bogus', '{}' from watch_items"
    ),
    "jobs.status": (
        "insert into jobs (kind, payload, status, max_attempts) values ('x', '{}', 'bogus', 1)"
    ),
    "chain_snapshots.status": (
        "insert into chain_snapshots (chain_id, city_id, movie_count, status) "
        "select c.id, ci.id, 1, 'bogus' from chains c, cities ci "
        "where c.code = 'harkins' and ci.code = 'phx'"
    ),
}


@pytest.mark.parametrize("column", sorted(BAD_ROWS))
async def test_the_database_rejects_values_outside_the_allowed_set(
    session: AsyncSession, column: str
) -> None:
    user, _ = await users.create_pending(session, telegram_user_id=7, chat_id=7, display_name=None)
    phx = await catalog.get_city_by_code(session, "phx")
    assert phx
    await watches.add(session, user_id=user.id, city_id=phx.id, query="dune")
    with pytest.raises(IntegrityError, match=r"ck_"):
        async with session.begin_nested():
            await session.execute(text(BAD_ROWS[column]))


async def test_deleting_a_movie_unlinks_watches_but_keeps_them(session: AsyncSession) -> None:
    user, _ = await users.create_pending(session, telegram_user_id=1, chat_id=1, display_name=None)
    phx = await catalog.get_city_by_code(session, "phx")
    harkins = await catalog.get_chain_by_code(session, "harkins")
    assert phx and harkins
    movie, _ = await movies.upsert_movie(
        session,
        harkins.id,
        phx.id,
        MovieListing("dune", "Dune", "https://example.test/dune", "now_showing"),
    )
    watch, _ = await watches.add(
        session, user_id=user.id, city_id=phx.id, query="dune", movie_id=movie.id
    )
    await session.execute(text("delete from movies"))
    kept = await watches.get(session, watch.id)
    assert kept is not None
    assert kept.movie_id is None  # ON DELETE SET NULL


async def test_deleting_a_user_removes_their_watches_signals_and_alerts(
    session: AsyncSession,
) -> None:
    user, _ = await users.create_pending(session, telegram_user_id=1, chat_id=1, display_name=None)
    phx = await catalog.get_city_by_code(session, "phx")
    assert phx
    watch, _ = await watches.add(session, user_id=user.id, city_id=phx.id, query="dune")
    await signals.record(session, watch.id, "listed", {})
    await alerts.try_record(session, user.id, "new_release:1")

    await session.execute(text("delete from users"))

    remaining = (
        await session.execute(
            text(
                "select (select count(*) from watch_items), (select count(*) from signals), "
                "(select count(*) from alerts)"
            )
        )
    ).one()
    assert tuple(remaining) == (0, 0, 0)  # ON DELETE CASCADE all the way down
