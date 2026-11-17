"""Signals seen for a watch (listed, dates, venues, sessions). Each stage is stored once."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables


async def record(
    session: AsyncSession, watch_item_id: int, stage: str, evidence: dict[str, Any]
) -> bool:
    """Store a signal. False means this stage was already recorded for the watch."""
    stmt = (
        insert(tables.Signal)
        .values(watch_item_id=watch_item_id, stage=stage, evidence=evidence)
        .on_conflict_do_nothing(constraint="uq_signals_watch_item_id_stage")
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


async def evidence_for(session: AsyncSession, watch_item_id: int, stage: str) -> dict[str, Any]:
    """What was seen when this stage was recorded (empty if it was not)."""
    value = await session.scalar(
        select(tables.Signal.evidence).where(
            tables.Signal.watch_item_id == watch_item_id, tables.Signal.stage == stage
        )
    )
    return dict(value) if value else {}
