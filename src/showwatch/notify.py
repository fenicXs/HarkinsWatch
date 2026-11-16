"""Send Telegram messages and buttons. Used by the bot and, from M3, by the worker."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# Telegram rejects the whole keyboard if any callback_data is longer than this.
CALLBACK_DATA_MAX_BYTES = 64

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Button:
    text: str
    data: str  # callback data; carries record ids, never list positions

    def __post_init__(self) -> None:
        if len(self.data.encode()) > CALLBACK_DATA_MAX_BYTES:
            raise ValueError(
                f"callback data is over {CALLBACK_DATA_MAX_BYTES} bytes: {self.data!r}"
            )


Buttons = Sequence[Sequence[Button]]  # rows of buttons


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool:
        """Send a message. Returns False if Telegram refused it (for example the user blocked us)."""
        ...


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(b.text, callback_data=b.data) for b in row]
                    for row in buttons
                ]
            )
        try:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        except TelegramError as error:
            log.warning("telegram_send_failed", chat_id=chat_id, error=str(error))
            return False
        return True
