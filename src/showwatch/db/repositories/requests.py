"""Parsed plain-language requests that wait for the user's confirm tap."""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables


async def create(
    session: AsyncSession, user_id: int, payload: dict[str, Any], expires_at: datetime
) -> int:
    row = tables.ParsedRequestRow(user_id=user_id, payload=payload, expires_at=expires_at)
    session.add(row)
    await session.flush()
    return row.id


async def take(
    session: AsyncSession, user_id: int, request_id: int, now: datetime
) -> dict[str, Any] | None:
    """The user's own unexpired request, removed so one tap cannot be replayed. None otherwise."""
    row = await session.scalar(
        select(tables.ParsedRequestRow)
        .where(
            tables.ParsedRequestRow.id == request_id,
            tables.ParsedRequestRow.user_id == user_id,
            tables.ParsedRequestRow.expires_at > now,
        )
        .with_for_update()
    )
    if row is None:
        return None
    payload = dict(row.payload)
    await session.delete(row)
    await session.flush()
    return payload


async def purge_expired(session: AsyncSession, now: datetime) -> int:
    result = await session.execute(
        delete(tables.ParsedRequestRow).where(tables.ParsedRequestRow.expires_at <= now)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
