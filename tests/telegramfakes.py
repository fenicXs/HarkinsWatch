"""Fakes for the Telegram side: a notifier that records messages, and a bot that never touches
the network, plus builders for the update JSON Telegram would send."""

from typing import Any

from telegram import User
from telegram.ext import ExtBot

from showwatch.notify import Buttons

FAKE_TOKEN = "123456789:" + "A" * 35  # token-shaped, not a real bot


class FakeNotifier:
    """Records what the handlers send. `sent` holds (chat_id, text, buttons)."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Buttons | None]] = []
        self.unreachable: set[int] = set()  # chats where sending fails, e.g. the user blocked us

    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool:
        if chat_id in self.unreachable:
            return False
        self.sent.append((chat_id, text, buttons))
        return True

    def to(self, chat_id: int) -> list[str]:
        return [text for cid, text, _ in self.sent if cid == chat_id]

    def buttons_to(self, chat_id: int) -> list[tuple[str, str]]:
        """(label, callback data) of every button sent to a chat, in order."""
        return [
            (b.text, b.data)
            for cid, _, rows in self.sent
            if cid == chat_id and rows
            for row in rows
            for b in row
        ]


class FakeBot(ExtBot):  # type: ignore[type-arg]
    """A python-telegram-bot Bot that records calls instead of making them."""

    def __init__(self) -> None:
        super().__init__(token=FAKE_TOKEN)
        # PTB bots are frozen: only underscore names can be set after construction.
        self._sent: list[tuple[int, str, Any]] = []
        self._answered: list[str] = []
        self._answer_error: Exception | None = None  # set to make answering fail

    @property
    def sent(self) -> list[tuple[int, str, Any]]:
        return self._sent

    @property
    def answered(self) -> list[str]:
        return self._answered

    async def initialize(self) -> None:
        self._bot_user = User(
            id=999, first_name="ShowWatch", is_bot=True, username="showwatch_test_bot"
        )

    async def shutdown(self) -> None:
        return None

    async def send_message(  # type: ignore[override]
        self, chat_id: int, text: str, *args: Any, reply_markup: Any = None, **kwargs: Any
    ) -> None:
        self._sent.append((chat_id, text, reply_markup))

    async def answer_callback_query(  # type: ignore[override]
        self, callback_query_id: str, *args: Any, **kwargs: Any
    ) -> bool:
        if self._answer_error is not None:
            raise self._answer_error
        self._answered.append(callback_query_id)
        return True

    def to(self, chat_id: int) -> list[str]:
        return [text for cid, text, _ in self.sent if cid == chat_id]

    def callback_data_to(self, chat_id: int) -> list[str]:
        return [
            button.callback_data
            for cid, _, markup in self.sent
            if cid == chat_id and markup
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]


def _user(user_id: int, name: str) -> dict[str, Any]:
    return {"id": user_id, "is_bot": False, "first_name": name}


def message_update(
    update_id: int, user_id: int, text: str, *, name: str = "Ann", chat_type: str = "private"
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": update_id,
        "date": 1_700_000_000,
        "chat": {"id": user_id if chat_type == "private" else -user_id, "type": chat_type},
        "from": _user(user_id, name),
        "text": text,
    }
    if text.startswith("/"):
        command = text.split()[0]
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
    return {"update_id": update_id, "message": message}


def callback_update(
    update_id: int, user_id: int, data: str, *, name: str = "Ann"
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": _user(user_id, name),
            "chat_instance": "test",
            "data": data,
            "message": {
                "message_id": update_id,
                "date": 1_700_000_000,
                "chat": {"id": user_id, "type": "private"},
                "text": "buttons",
            },
        },
    }
