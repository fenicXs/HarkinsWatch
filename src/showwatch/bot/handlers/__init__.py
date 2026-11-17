"""Handler logic. Each function takes the shared `Env` and a `Sender`, never Telegram objects,
so it can be tested with a fake notifier and a real database; `bot/app.py` does the adapting."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.llm.client import LLMClient
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
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    llm: LLMClient | None = None  # plain-language requests; None when not configured
