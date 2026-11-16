from typing import Any

from telegram.error import Forbidden

from showwatch.notify import Button, TelegramNotifier


class _RecordingBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    async def send_message(self, **kwargs: Any) -> None:
        if self._error:
            raise self._error
        self.calls.append(kwargs)


async def test_a_plain_message_is_sent_without_a_keyboard() -> None:
    bot = _RecordingBot()
    assert await TelegramNotifier(bot).send(5, "hello") is True  # type: ignore[arg-type]
    assert bot.calls == [{"chat_id": 5, "text": "hello", "reply_markup": None}]


async def test_buttons_become_an_inline_keyboard_with_their_callback_data() -> None:
    bot = _RecordingBot()
    rows = [[Button("Approve", "approve:1"), Button("Deny", "deny:1")], [Button("More", "more:2")]]
    await TelegramNotifier(bot).send(5, "pick", rows)  # type: ignore[arg-type]
    markup = bot.calls[0]["reply_markup"]
    assert [[b.callback_data for b in row] for row in markup.inline_keyboard] == [
        ["approve:1", "deny:1"],
        ["more:2"],
    ]
    assert markup.inline_keyboard[0][0].text == "Approve"


async def test_a_refused_message_is_reported_not_raised() -> None:
    """For example the user blocked the bot: the caller gets False and carries on."""
    bot = _RecordingBot(error=Forbidden("bot was blocked by the user"))
    assert await TelegramNotifier(bot).send(5, "hello") is False  # type: ignore[arg-type]
