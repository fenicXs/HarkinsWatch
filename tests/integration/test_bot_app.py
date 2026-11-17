"""The Telegram adapter end to end: real python-telegram-bot routing, a fake bot, a real database."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram.error import BadRequest

from conftest import ADMIN_ID
from showwatch.bot.app import HELP_TEXT, App, build_application, on_startup, sender_from
from showwatch.bot.handlers.access import ACCESS_REQUESTED
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
    offered = bot.callback_data_to(ADMIN_ID)[2:]
    assert [d.split(":")[0] for d in offered] == ["watchpick", "watchfor"]
    movie_button = offered[0]

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


async def test_updates_without_a_sender_are_ignored_and_not_recorded(
    application: App, bot: FakeBot
) -> None:
    data = message_update(9, 111, "/start")
    del data["message"]["from"]  # e.g. posted on behalf of a channel
    await _feed(application, data)
    assert bot.sent == []
    assert await _status(application, 111) is None


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


async def test_if_the_gate_itself_fails_the_update_is_dropped(
    application: App, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PTB carries on to the next handler after an exception unless told to stop."""

    async def broken_gate(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database is down")

    monkeypatch.setattr("showwatch.bot.handlers.access.gate", broken_gate)
    await _feed(application, message_update(1, 111, "/start"))
    await _feed(application, message_update(2, 111, "hello"))
    assert bot.sent == []  # no "You're set up", no help text


async def _prepare_admin_with_a_movie(application: App) -> None:
    """The admin is in Phoenix and Phoenix lists one coming-soon movie, Dune."""
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
        admin = await users.get_by_telegram_id(session, ADMIN_ID)
        assert admin
        await users.set_current_city(session, admin.id, phx.id)


async def test_a_denied_user_gets_nothing_through_the_real_routing(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 111, "/start", name="Ann"))
    deny = bot.callback_data_to(ADMIN_ID)[1]
    await _feed(application, callback_update(2, ADMIN_ID, deny, name="Root"))
    bot.sent.clear()

    for number, text in enumerate(["/start", "/list", "/watch dune", "hello"], start=3):
        await _feed(application, message_update(number, 111, text))

    assert await _status(application, 111) == "denied"
    assert bot.to(111) == []


async def test_a_pending_user_only_ever_hears_the_access_reply(
    application: App, bot: FakeBot
) -> None:
    for number, text in enumerate(["/start", "hello", "/nonsense", "/list"], start=1):
        await _feed(application, message_update(number, 111, text))
    assert bot.to(111) == [ACCESS_REQUESTED]  # no help text, no "You're set up"


async def test_an_approved_user_who_is_not_the_admin_cannot_approve_anyone(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 222, "/start", name="Bo"))
    await _feed(application, message_update(2, 111, "/start", name="Ann"))
    approve_bo, _ = bot.callback_data_to(ADMIN_ID)[:2]
    approve_ann = bot.callback_data_to(ADMIN_ID)[2]
    await _feed(application, callback_update(3, ADMIN_ID, approve_bo, name="Root"))
    assert await _status(application, 222) == "approved"

    await _feed(application, callback_update(4, 222, approve_ann, name="Bo"))  # Bo, not the admin

    assert await _status(application, 111) == "pending"


async def test_a_command_addressed_to_the_bot_by_name_still_works(
    application: App, bot: FakeBot
) -> None:
    await _prepare_admin_with_a_movie(application)
    await _feed(
        application, message_update(1, ADMIN_ID, "/watch@showwatch_test_bot dune", name="Root")
    )
    assert bot.callback_data_to(ADMIN_ID)[0].startswith("watchpick:")


async def test_none_of_these_saves_a_watch_for_the_typed_words(
    application: App, bot: FakeBot
) -> None:
    await _prepare_admin_with_a_movie(application)
    await _feed(application, message_update(1, ADMIN_ID, "/watch dune messiah", name="Root"))
    assert "watchfor:dune messiah" in bot.callback_data_to(ADMIN_ID)

    await _feed(application, callback_update(2, ADMIN_ID, "watchfor:dune messiah", name="Root"))
    await _feed(application, message_update(3, ADMIN_ID, "/list", name="Root"))

    assert "dune messiah: waiting for it to be listed" in bot.to(ADMIN_ID)[-1]


async def test_a_button_still_works_when_telegram_refuses_to_acknowledge_it(
    application: App, bot: FakeBot
) -> None:
    bot._answer_error = BadRequest("Query is too old and response timeout expired")
    await _feed(application, callback_update(1, ADMIN_ID, "city:phx", name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "City set to Phoenix."


@pytest.mark.parametrize("data", ["approve:²", "approve:١٢", "unwatch:٣"])
async def test_digit_look_alikes_in_button_data_are_ignored(
    application: App, bot: FakeBot, data: str
) -> None:
    await _feed(application, callback_update(1, ADMIN_ID, data, name="Root"))
    assert bot.sent == []  # int() would have raised on these


async def test_startup_asks_the_admin_again_about_requests_still_waiting(
    application: App, bot: FakeBot
) -> None:
    await _feed(application, message_update(1, 111, "/start", name="Ann"))
    bot.sent.clear()

    await on_startup(application)  # e.g. the bot restarted

    assert len(bot.to(ADMIN_ID)) == 1
    assert "Ann" in bot.to(ADMIN_ID)[0]
    assert [d.split(":")[0] for d in bot.callback_data_to(ADMIN_ID)] == ["approve", "deny"]


async def test_the_search_buttons_are_routed_to_the_search_handlers(
    application: App, bot: FakeBot
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    factory = application.bot_data["env"].session_factory
    async with factory() as session, session.begin():
        chain = await catalog.get_chain_by_code(session, "harkins")
        phx = await catalog.get_city_by_code(session, "phx")
        assert chain and phx
        movie, _ = await movies.upsert_movie(
            session,
            chain.id,
            phx.id,
            MovieListing("moana", "Moana", "https://example.test/m", "now_showing"),
        )
        admin = await users.get_by_telegram_id(session, ADMIN_ID)
        assert admin
        await users.set_current_city(session, admin.id, phx.id)
    today = datetime.now(ZoneInfo("America/Phoenix")).date().isoformat()

    await _feed(application, callback_update(1, ADMIN_ID, f"pick:{movie.id}", name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "Moana: pick a date."
    assert bot.callback_data_to(ADMIN_ID)[0] == f"date:{movie.id}:{today}"

    await _feed(application, callback_update(2, ADMIN_ID, f"date:{movie.id}:{today}", name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "Looking up showtimes…"

    await _feed(
        application, callback_update(3, ADMIN_ID, f"fmt:{movie.id}:{today}:deadbeef", name="Root")
    )
    assert bot.to(ADMIN_ID)[-1] == "Those showtimes changed. Pick the date again."

    await _feed(application, callback_update(4, ADMIN_ID, "book:5", name="Root"))
    assert bot.to(ADMIN_ID)[-1] == "That showtime is no longer available."


@pytest.mark.parametrize(
    "data",
    [
        "date:notanumber:2026-11-17",
        "date:5:not-a-date",
        "date:5",
        "fmt:5:2026-11-17",
        "fmt:5:bad:0",
        "fmt:5:2026-11-17:0",
        "fmt:5:2026-11-17:ZZZZZZZZ",
        "fmt:5:2026-11-17:abc",
        "fmt:x:2026-11-17:0",
        "pick:x",
        "book:\u0663",
    ],
)
async def test_malformed_search_buttons_are_ignored(
    application: App, bot: FakeBot, data: str
) -> None:
    await _feed(application, callback_update(1, ADMIN_ID, data, name="Root"))
    assert bot.sent == []
    assert bot.answered == ["cb1"]
