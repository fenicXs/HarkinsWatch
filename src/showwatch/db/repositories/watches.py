"""Watch items: what each user is waiting for."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables
from showwatch.domain.models import WatchItem, WatchStage


async def get(session: AsyncSession, watch_item_id: int) -> WatchItem | None:
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
    existing = await session.scalar(
        select(tables.WatchItem).where(
            tables.WatchItem.user_id == user_id,
            tables.WatchItem.city_id == city_id,
            tables.WatchItem.query == query,
        )
    )
    if existing is not None:
        return WatchItem.model_validate(existing), False
    row = tables.WatchItem(
        user_id=user_id, city_id=city_id, query=query, movie_id=movie_id, format_pref=format_pref
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return WatchItem.model_validate(row), True


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
