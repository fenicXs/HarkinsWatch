"""The Telegram adapter: turns python-telegram-bot updates into calls to the handler logic.

Everything of substance lives in `bot/handlers/`; this file only extracts who sent an update,
runs the access gate before every handler, and routes commands and button taps.
"""

from datetime import date
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ExtBot,
    MessageHandler,
    TypeHandler,
    filters,
)

from showwatch import __version__
from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender, access, search, watch
from showwatch.core.db import check_connection
from showwatch.core.settings import Settings
from showwatch.notify import TelegramNotifier

log = structlog.get_logger(__name__)

App = Application[Any, Any, Any, Any, Any, Any]
Context = ContextTypes.DEFAULT_TYPE

ENV_KEY = "env"
ENGINE_KEY = "engine"
HELP_TEXT = "Try /watch <title>, /list, /unwatch or /city."
ALLOWED_UPDATES = ["message", "callback_query"]


def sender_from(update: Update) -> Sender | None:
    """The sender of a private-chat update, or None for anything else (groups, channels, bots)."""
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None or chat.type != "private" or user.is_bot:
        return None
    return Sender(
        telegram_user_id=user.id, chat_id=chat.id, display_name=user.full_name or user.username
    )


def _env(context: Context) -> Env:
    env: Env = context.application.bot_data[ENV_KEY]
    return env


def _sender(update: Update) -> Sender:
    sender = sender_from(update)
    assert sender is not None  # the gate already stopped updates without one
    return sender


async def _gate(update: Update, context: Context) -> None:
    """Runs first for every update; stops it unless the sender is allowed through."""
    sender = sender_from(update)
    if sender is None:
        raise ApplicationHandlerStop
    try:
        decision = await access.gate(_env(context), sender)
    except Exception:
        # python-telegram-bot carries on to the next handler after an exception, which would let
        # an unchecked user through. Fail closed instead.
        log.exception("gate_failed")
        raise ApplicationHandlerStop from None
    if decision == "stop":
        raise ApplicationHandlerStop


async def _start(update: Update, context: Context) -> None:
    await access.handle_start(_env(context), _sender(update))


async def _city(update: Update, context: Context) -> None:
    await watch.handle_city(_env(context), _sender(update))


async def _watch(update: Update, context: Context) -> None:
    await watch.handle_watch(_env(context), _sender(update), " ".join(context.args or []))


async def _list(update: Update, context: Context) -> None:
    await watch.handle_list(_env(context), _sender(update))


async def _unwatch(update: Update, context: Context) -> None:
    await watch.handle_unwatch(_env(context), _sender(update))


async def _help(update: Update, context: Context) -> None:
    await _env(context).notifier.send(_sender(update).chat_id, HELP_TEXT)


async def _button(update: Update, context: Context) -> None:
    """Route a button tap by its `<action>:<id>` data."""
    query = update.callback_query
    assert query is not None
    try:
        await query.answer()  # stops the spinner on the user's button
    except TelegramError as error:  # for example an old button: the action can still run
        log.info("answer_callback_failed", error=str(error))
    parsed = keyboards.parse_callback(query.data or "")
    if parsed is None:
        return
    action, ref = parsed
    env, sender = _env(context), _sender(update)
    if action == keyboards.CITY:
        await watch.handle_city_pick(env, sender, ref)
    elif action == keyboards.WATCHFOR:
        await watch.handle_watch_for(env, sender, ref)
    elif action == keyboards.DATE:
        await _date_button(env, sender, ref)
    elif action == keyboards.FMT:
        await _format_button(env, sender, ref)
    elif ref.isascii() and ref.isdigit():
        number = int(ref)
        if action == keyboards.APPROVE:
            await access.handle_decision(env, sender, "approve", number)
        elif action == keyboards.DENY:
            await access.handle_decision(env, sender, "deny", number)
        elif action == keyboards.WATCHPICK:
            await watch.handle_watch_pick(env, sender, number)
        elif action == keyboards.UNWATCH:
            await watch.handle_unwatch_pick(env, sender, number)
        elif action == keyboards.PICK:
            await search.handle_pick(env, sender, number)
        elif action == keyboards.BOOK:
            await search.handle_book(env, sender, number)


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


async def _date_button(env: Env, sender: Sender, ref: str) -> None:
    """`date:<movie_id>:<YYYY-MM-DD>`"""
    movie_id, _, day_text = ref.partition(":")
    day = _parse_day(day_text)
    if movie_id.isascii() and movie_id.isdigit() and day is not None:
        await search.handle_date(env, sender, int(movie_id), day)


async def _format_button(env: Env, sender: Sender, ref: str) -> None:
    """`fmt:<movie_id>:<YYYY-MM-DD>:<n>`"""
    parts = ref.split(":")
    if len(parts) != 3 or not all(p.isascii() and p.isdigit() for p in (parts[0], parts[2])):
        return
    day = _parse_day(parts[1])
    if day is not None:
        await search.handle_format(env, sender, int(parts[0]), day, int(parts[2]))


async def on_startup(application: App) -> None:
    """Runs once before polling starts (PTB's `post_init`)."""
    engine: AsyncEngine | None = application.bot_data.get(ENGINE_KEY)
    if engine is not None:
        await check_connection(engine)
    await access.ensure_admin(_env_of(application))
    try:
        await access.send_pending_requests(_env_of(application))
    except Exception:  # never stop the bot from starting over a notification
        log.exception("pending_requests_failed")
    log.info("ready", service="bot", version=__version__)


async def on_shutdown(application: App) -> None:
    log.info("stopping", service="bot")
    engine: AsyncEngine | None = application.bot_data.get(ENGINE_KEY)
    if engine is not None:
        await engine.dispose()


def _env_of(application: App) -> Env:
    env: Env = application.bot_data[ENV_KEY]
    return env


def build_application(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    engine: AsyncEngine | None = None,
    bot: ExtBot[None] | None = None,
) -> App:
    """Wire the handlers. `bot` lets tests replace the Telegram connection."""
    token, admin_id = settings.require_bot()
    builder = ApplicationBuilder().post_init(on_startup).post_shutdown(on_shutdown)
    builder = builder.bot(bot) if bot is not None else builder.token(token.get_secret_value())
    application: App = builder.build()
    application.bot_data[ENV_KEY] = Env(
        session_factory=session_factory,
        notifier=TelegramNotifier(application.bot),
        admin_telegram_user_id=admin_id,
    )
    if engine is not None:
        application.bot_data[ENGINE_KEY] = engine

    application.add_handler(TypeHandler(Update, _gate), group=-1)  # first, for every update
    application.add_handler(CommandHandler("start", _start))
    application.add_handler(CommandHandler("city", _city))
    application.add_handler(CommandHandler("watch", _watch))
    application.add_handler(CommandHandler("list", _list))
    application.add_handler(CommandHandler("unwatch", _unwatch))
    application.add_handler(CallbackQueryHandler(_button))
    application.add_handler(MessageHandler(filters.TEXT, _help))  # other text and unknown commands
    return application
