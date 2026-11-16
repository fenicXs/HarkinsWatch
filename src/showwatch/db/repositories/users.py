"""Users and their access status. Callers own the transaction; functions only flush."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import tables
from showwatch.domain.models import User, UserStatus


async def get(session: AsyncSession, user_id: int) -> User | None:
    row = await session.get(tables.User, user_id)
    return User.model_validate(row) if row else None


async def get_by_telegram_id(session: AsyncSession, telegram_user_id: int) -> User | None:
    row = await session.scalar(
        select(tables.User).where(tables.User.telegram_user_id == telegram_user_id)
    )
    return User.model_validate(row) if row else None


async def create_pending(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    chat_id: int | None,
    display_name: str | None,
) -> tuple[User, bool]:
    """Return the user for this Telegram id, creating a `pending` one if none exists.

    The bool is True when a new row was created.
    """
    row = await session.scalar(
        select(tables.User).where(tables.User.telegram_user_id == telegram_user_id)
    )
    if row is not None:
        return User.model_validate(row), False
    row = tables.User(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        display_name=display_name,
        status="pending",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return User.model_validate(row), True


async def upsert_admin(
    session: AsyncSession, *, telegram_user_id: int, display_name: str | None = None
) -> User:
    """Make sure the admin exists, is approved and flagged `is_admin`."""
    row = await session.scalar(
        select(tables.User).where(tables.User.telegram_user_id == telegram_user_id)
    )
    if row is None:
        row = tables.User(telegram_user_id=telegram_user_id, display_name=display_name)
        session.add(row)
    row.status = "approved"
    row.is_admin = True
    await session.flush()
    await session.refresh(row)
    return User.model_validate(row)


async def set_status(session: AsyncSession, user_id: int, status: UserStatus) -> User:
    row = await session.get(tables.User, user_id)
    if row is None:
        raise LookupError(f"user {user_id} not found")
    row.status = status
    await session.flush()
    await session.refresh(row)
    return User.model_validate(row)


async def set_chat_id(session: AsyncSession, user_id: int, chat_id: int) -> None:
    row = await session.get(tables.User, user_id)
    if row is None:
        raise LookupError(f"user {user_id} not found")
    row.chat_id = chat_id
    await session.flush()


async def mark_pending_notified(session: AsyncSession, user_id: int) -> None:
    row = await session.get(tables.User, user_id)
    if row is None:
        raise LookupError(f"user {user_id} not found")
    row.pending_notified = True
    await session.flush()


async def set_current_city(session: AsyncSession, user_id: int, city_id: int) -> None:
    row = await session.get(tables.User, user_id)
    if row is None:
        raise LookupError(f"user {user_id} not found")
    row.current_city_id = city_id
    await session.flush()


async def list_approved_in_city(session: AsyncSession, city_id: int) -> list[User]:
    rows = await session.scalars(
        select(tables.User)
        .where(tables.User.status == "approved", tables.User.current_city_id == city_id)
        .order_by(tables.User.id)
    )
    return [User.model_validate(r) for r in rows]
