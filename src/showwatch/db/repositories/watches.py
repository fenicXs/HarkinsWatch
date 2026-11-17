"""Watch items: what each user is waiting for."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Text, and_, case, cast, delete, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables
from showwatch.domain.models import WatchItem, WatchStage


async def get(session: AsyncSession, watch_item_id: int) -> WatchItem | None:
    """Fetch any watch by id. For worker-side code only: it is not scoped to a user.

    Handlers acting on an id a user sent must use `list_for_user` or `delete_for_user`.
    """
    row = await session.get(tables.WatchItem, watch_item_id)
    return WatchItem.model_validate(row) if row else None


async def add(
    session: AsyncSession,
    *,
    user_id: int,
    city_id: int,
    query: str,
    movie_id: int | None = None,
    format_pref: str | None = None,
) -> tuple[WatchItem, bool]:
    """Save a watch. Returns the existing one (and False) if this user already watches the query."""
    inserted_id = await session.scalar(
        insert(tables.WatchItem)
        .values(
            user_id=user_id,
            city_id=city_id,
            query=query,
            movie_id=movie_id,
            format_pref=format_pref,
        )
        .on_conflict_do_nothing(constraint="uq_watch_items_user_id_city_id_query")
        .returning(tables.WatchItem.id)
    )
    row = await session.scalar(
        select(tables.WatchItem).where(
            tables.WatchItem.user_id == user_id,
            tables.WatchItem.city_id == city_id,
            tables.WatchItem.query == query,
        )
    )
    assert row is not None
    return WatchItem.model_validate(row), inserted_id is not None


async def list_for_user(session: AsyncSession, user_id: int) -> list[WatchItem]:
    rows = await session.scalars(
        select(tables.WatchItem)
        .where(tables.WatchItem.user_id == user_id)
        .order_by(tables.WatchItem.id)
    )
    return [WatchItem.model_validate(r) for r in rows]


async def delete_for_user(session: AsyncSession, user_id: int, watch_item_id: int) -> bool:
    """Delete one of the user's own watches. False if it does not exist or is someone else's."""
    result = await session.execute(
        delete(tables.WatchItem).where(
            tables.WatchItem.id == watch_item_id, tables.WatchItem.user_id == user_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def list_by_stage(
    session: AsyncSession, stages: Sequence[WatchStage], *, city_id: int | None = None
) -> list[WatchItem]:
    stmt = select(tables.WatchItem).where(tables.WatchItem.stage.in_(stages))
    if city_id is not None:
        stmt = stmt.where(tables.WatchItem.city_id == city_id)
    rows = await session.scalars(stmt.order_by(tables.WatchItem.id))
    return [WatchItem.model_validate(r) for r in rows]


async def list_unmatched(session: AsyncSession, city_id: int) -> list[WatchItem]:
    """Watches in this city that have no movie yet and are not finished."""
    rows = await session.scalars(
        select(tables.WatchItem)
        .where(
            tables.WatchItem.city_id == city_id,
            tables.WatchItem.movie_id.is_(None),
            tables.WatchItem.stage != "done",
        )
        .order_by(tables.WatchItem.id)
    )
    return [WatchItem.model_validate(r) for r in rows]


async def set_movie(session: AsyncSession, watch_item_id: int, movie_id: int) -> None:
    row = await session.get(tables.WatchItem, watch_item_id)
    if row is None:
        raise LookupError(f"watch item {watch_item_id} not found")
    row.movie_id = movie_id
    await session.flush()


async def set_stage(
    session: AsyncSession,
    watch_item_id: int,
    stage: WatchStage,
    *,
    fast_until: datetime | None = None,
) -> WatchItem:
    row = await session.get(tables.WatchItem, watch_item_id)
    if row is None:
        raise LookupError(f"watch item {watch_item_id} not found")
    row.stage = stage
    if fast_until is not None:
        row.fast_until = fast_until
    await session.flush()
    await session.refresh(row)
    return WatchItem.model_validate(row)


async def set_format_pref(
    session: AsyncSession, watch_item_id: int, format_pref: str | None
) -> None:
    row = await session.get(tables.WatchItem, watch_item_id)
    if row is None:
        raise LookupError(f"watch item {watch_item_id} not found")
    row.format_pref = format_pref
    await session.flush()


async def unalerted_stages(
    session: AsyncSession, city_id: int
) -> list[tuple[WatchItem, str, str, dict[str, Any]]]:
    """Watch alerts still owed: a recorded signal whose alert has not been delivered.

    Deciding an alert and delivering it are separate steps, and Telegram can refuse a message,
    so this is how an undelivered alert is found again on the next check. An older `listed`
    alert is dropped once the movie has gone on sale: only the `sessions` alert matters then.
    """
    alert_key = (
        literal("watch_open:")
        + cast(tables.WatchItem.id, Text)
        + literal(":")
        + tables.Signal.stage
        + case((tables.Signal.source != "", literal(":") + tables.Signal.source), else_=literal(""))
    )
    rows = await session.execute(
        select(tables.WatchItem, tables.Signal.stage, tables.Signal.source, tables.Signal.evidence)
        .join(tables.Signal, tables.Signal.watch_item_id == tables.WatchItem.id)
        .outerjoin(
            tables.Alert,
            and_(
                tables.Alert.user_id == tables.WatchItem.user_id,
                tables.Alert.dedupe_key == alert_key,
            ),
        )
        .where(
            tables.WatchItem.city_id == city_id,
            tables.Signal.stage.in_(("listed", "sessions")),
            tables.Alert.id.is_(None),
        )
        .order_by(tables.WatchItem.id, tables.Signal.id)
    )
    owed = [
        (WatchItem.model_validate(item), stage, source, dict(evidence))
        for item, stage, source, evidence in rows.all()
    ]
    on_sale = {item.id for item, stage, _, _ in owed if stage == "sessions"}
    return [o for o in owed if not (o[1] == "listed" and o[0].id in on_sale)]
