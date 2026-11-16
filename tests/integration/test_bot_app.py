"""The Telegram adapter end to end: real python-telegram-bot routing, a fake bot, a real database."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update

from conftest import ADMIN_ID
from showwatch.bot.app import HELP_TEXT, App, build_application, on_startup, sender_from
from showwatch.core.settings import Settings
from showwatch.db.repositories import catalog, movies, users
from showwatch.domain.models import MovieListing
from telegramfakes import FAKE_TOKEN, FakeBot, callback_update, message_update


@pytest.fixture
async def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
async def application(
    session_factory: async_sessionmaker[AsyncSession], bot: FakeBot
) -> AsyncIterator[App]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+asyncpg://unused",
        telegram_bot_token=FAKE_TOKEN,
        admin_telegram_user_id=ADMIN_ID,
    )
    app = build_application(settings, session_factory, bot=bot)
    await app.initialize()
    await on_startup(app)
    yield app
    await app.shutdown()


async def _feed(app: App, data: dict) -> None:  # type: ignore[type-arg]
    await app.process_update(Update.de_json(data, app.bot))  # type: ignore[arg-type]


async def _status(app: App, telegram_user_id: int) -> str | None:
    factory = app.bot_data["env"].session_factory
    async with factory() as session:
        user = await users.get_by_telegram_id(session, telegram_user_id)
    return user.status if user else None


async def test_a_stranger_is_held_for_approval_and_the_admin_is_asked(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 111, "/start", name="Ann"))

    assert await _status(application, 111) == "pending"
    assert bot.to(111) == ["Access requested; waiting for approval."]
    assert len(bot.to(ADMIN_ID)) == 1
    assert [d.split(":")[0] for d in bot.callback_data_to(ADMIN_ID)] == ["approve", "deny"]


async def test_a_button_tap_from_someone_not_approved_is_dropped(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 111, "/start"))  # Ann is pending
    ann_id = bot.callback_data_to(ADMIN_ID)[0].split(":")[1]

    await _feed(application, callback_update(2, 111, f"approve:{ann_id}"))  # Ann approves herself

    assert await _status(application, 111) == "pending"
    assert bot.answered == []  # not even acknowledged: the update never reached a handler


async def test_the_admin_approves_with_a_button_and_the_user_can_then_use_the_bot(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 111, "/start", name="Ann"))
    approve = bot.callback_data_to(ADMIN_ID)[0]

    await _feed(application, callback_update(2, ADMIN_ID, approve, name="Root"))

    assert await _status(application, 111) == "approved"
    assert bot.answered == ["cb2"]  # the admin's spinner was stopped
    assert "approved" in bot.to(111)[-1].lower()

    await _feed(application, message_update(3, 111, "/start", name="Ann"))
    assert "You're set up" in bot.to(111)[-1]


async def test_commands_reach_their_handlers_with_their_arguments(
    application: App, bot: FakeBot
) -> None:
    factory = application.bot_data["env"].session_factory
    async with factory() as session, session.begin():
        chain = await catalog.get_chain_by_code(session, "harkins")
        phx = await catalog.get_city_by_code(session, "phx")
        assert chain and phx
        await movies.upsert_movie(
            session,
            chain.id,
            phx.id,
            MovieListing("dune", "DUNE: Part Three", "https://example.test/dune", "coming_soon"),
        )
    await _feed(application, message_update(1, ADMIN_ID, "/city", name="Root"))
    assert bot.callback_data_to(ADMIN_ID) == ["city:phx", "city:blr"]

    await _feed(application, callback_update(2, ADMIN_ID, "city:phx", name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "City set to Phoenix."

    await _feed(application, message_update(3, ADMIN_ID, "/watch dune", name="Root"))
    (movie_button,) = bot.callback_data_to(ADMIN_ID)[2:]
    assert movie_button.startswith("watchpick:")

    await _feed(application, callback_update(4, ADMIN_ID, movie_button, name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "Watching DUNE: Part Three."

    await _feed(application, message_update(5, ADMIN_ID, "/list", name="Root"))
    assert "DUNE: Part Three: waiting for it to be listed" in bot.to(ADMIN_ID)[-1]

    await _feed(application, message_update(6, ADMIN_ID, "/unwatch", name="Root"))
    removal = bot.callback_data_to(ADMIN_ID)[-1]
    await _feed(application, callback_update(7, ADMIN_ID, removal, name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "Removed DUNE: Part Three."


async def test_other_text_and_unknown_commands_get_the_help_line(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, ADMIN_ID, "hello there", name="Root"))
    await _feed(application, message_update(2, ADMIN_ID, "/nonsense", name="Root"))
    assert bot.to(ADMIN_ID) == [HELP_TEXT, HELP_TEXT]


async def test_malformed_button_data_is_acknowledged_and_ignored(
    application: App, bot: FakeBot
) -> None:
    for number, data in enumerate(["garbage", "approve:notanumber", "unknown:5"], start=1):
        await _feed(application, callback_update(number, ADMIN_ID, data, name="Root"))
    assert bot.answered == ["cb1", "cb2", "cb3"]
    assert bot.sent == []


async def test_group_chats_are_ignored(application: App, bot: FakeBot) -> None:
    await _feed(application, message_update(1, 111, "/start", chat_type="group"))
    assert await _status(application, 111) is None  # not even recorded
    assert bot.sent == []


async def test_updates_without_a_person_are_ignored(application: App, bot: FakeBot) -> None:
    await _feed(
        application,
        {
            "update_id": 9,
            "edited_message": {"message_id": 1, "date": 1, "chat": {"id": 5, "type": "channel"}},
        },
    )
    assert bot.sent == []


def test_sender_from_reads_private_chats_only() -> None:
    private = Update.de_json(message_update(1, 42, "hi", name="Ann"), None)
    group = Update.de_json(message_update(2, 42, "hi", chat_type="group"), None)
    assert private is not None and group is not None
    sender = sender_from(private)
    assert sender is not None
    assert (sender.telegram_user_id, sender.chat_id, sender.display_name) == (42, 42, "Ann")
    assert sender_from(group) is None


async def test_startup_creates_the_admin(application: App) -> None:
    assert await _status(application, ADMIN_ID) == "approved"
