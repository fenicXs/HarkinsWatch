"""Signals seen for a watch (listed, dates, venues, sessions). Each stage is stored once."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables


async def record(
    session: AsyncSession,
    watch_item_id: int,
    stage: str,
    evidence: dict[str, Any],
    source: str = "",
) -> bool:
    """Store a signal. False means this stage was already recorded for the watch (and source)."""
    stmt = (
        insert(tables.Signal)
        .values(watch_item_id=watch_item_id, stage=stage, source=source, evidence=evidence)
        .on_conflict_do_nothing(constraint="uq_signals_watch_item_id_stage_source")
        .returning(tables.Signal.id)
    )
    return (await session.scalar(stmt)) is not None


async def stages_seen(session: AsyncSession, watch_item_id: int) -> list[str]:
    rows = await session.scalars(
        select(tables.Signal.stage)
        .where(tables.Signal.watch_item_id == watch_item_id)
        .order_by(tables.Signal.id)
    )
    return list(rows)


async def sessions_sources(session: AsyncSession, watch_item_ids: list[int]) -> dict[int, set[str]]:
    """For each watch, the chains that have already told it tickets are open."""
    found: dict[int, set[str]] = {item_id: set() for item_id in watch_item_ids}
    if not watch_item_ids:
        return found
    rows = await session.execute(
        select(tables.Signal.watch_item_id, tables.Signal.source).where(
            tables.Signal.watch_item_id.in_(watch_item_ids), tables.Signal.stage == "sessions"
        )
    )
    for item_id, source in rows.all():
        found[item_id].add(source)
    return found
