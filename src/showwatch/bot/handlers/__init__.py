"""Handler logic. Each function takes the shared `Env` and a `Sender`, never Telegram objects,
so it can be tested with a fake notifier and a real database; `bot/app.py` does the adapting."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.notify import Notifier


@dataclass(frozen=True)
class Sender:
    """Who sent an update (private chats only)."""

    telegram_user_id: int
    chat_id: int
    display_name: str | None


@dataclass(frozen=True)
class Env:
    session_factory: async_sessionmaker[AsyncSession]
    notifier: Notifier
    admin_telegram_user_id: int
