"""Alert de-duplication: one row per (user, dedupe_key)."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables


async def try_record(session: AsyncSession, user_id: int, dedupe_key: str) -> bool:
    """Record that an alert is about to be sent. False means it was already sent: skip it."""
    stmt = (
        insert(tables.Alert)
        .values(user_id=user_id, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(constraint="uq_alerts_user_id_dedupe_key")
        .returning(tables.Alert.id)
    )
    return (await session.scalar(stmt)) is not None
