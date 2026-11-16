"""Who may use the bot: the gate, /start, and the admin's approve/deny buttons.

Rules (docs/plan.md, M2): an unknown user is recorded as `pending` and the admin is asked;
a pending user gets one "access requested" reply and silence after; a denied user gets
silence; an approved user gets everything.
"""

from dataclasses import dataclass, field
from typing import Literal

import structlog

from showwatch.bot import keyboards
from showwatch.bot.handlers import Env, Sender
from showwatch.db.repositories import users
from showwatch.notify import Buttons

log = structlog.get_logger(__name__)

Decision = Literal["allow", "stop"]
ACCESS_REQUESTED = "Access requested; waiting for approval."


@dataclass
class _Outbox:
    """Messages to send after the transaction commits."""

    messages: list[tuple[int, str, Buttons | None]] = field(default_factory=list)

    def add(self, chat_id: int, text: str, buttons: Buttons | None = None) -> None:
        self.messages.append((chat_id, text, buttons))


async def _flush(env: Env, outbox: _Outbox) -> None:
    for chat_id, text, buttons in outbox.messages:
        await env.notifier.send(chat_id, text, buttons)


def _name(display_name: str | None, telegram_user_id: int) -> str:
    return display_name or f"user {telegram_user_id}"


async def ensure_admin(env: Env) -> None:
    """Make sure the admin from the settings exists, is approved and flagged as admin."""
    async with env.session_factory() as session, session.begin():
        await users.upsert_admin(session, telegram_user_id=env.admin_telegram_user_id)


async def gate(env: Env, sender: Sender) -> Decision:
    """Decide whether this update may reach a handler. Runs before every other handler."""
    outbox = _Outbox()
    async with env.session_factory() as session, session.begin():
        user, created = await users.create_pending(
            session,
            telegram_user_id=sender.telegram_user_id,
            chat_id=sender.chat_id,
            display_name=sender.display_name,
        )
        if user.status == "approved":
            if user.chat_id != sender.chat_id:
                await users.set_chat_id(session, user.id, sender.chat_id)
            return "allow"

        if user.status == "pending" and not user.pending_notified:
            await users.mark_pending_notified(session, user.id)
            outbox.add(sender.chat_id, ACCESS_REQUESTED)
            if created:
                log.info("access_requested", user_id=user.id)
                outbox.add(
                    env.admin_telegram_user_id,  # a private chat has the same id as its user
                    f"{_name(sender.display_name, sender.telegram_user_id)} "
                    f"(id {sender.telegram_user_id}) asks for access.",
                    keyboards.approve_deny(user.id),
                )
    await _flush(env, outbox)
    return "stop"


async def handle_start(env: Env, sender: Sender) -> None:
    await env.notifier.send(
        sender.chat_id,
        "You're set up. Choose your city with /city, then watch a movie with /watch <title>.",
    )


async def handle_decision(
    env: Env, sender: Sender, action: Literal["approve", "deny"], target_user_id: int
) -> None:
    """The admin tapped Approve or Deny. Anyone else is ignored."""
    if sender.telegram_user_id != env.admin_telegram_user_id:
        log.warning("decision_from_non_admin", telegram_user_id=sender.telegram_user_id)
        return
    outbox = _Outbox()
    async with env.session_factory() as session, session.begin():
        target = await users.get(session, target_user_id)
        if target is None:
            outbox.add(sender.chat_id, "That user no longer exists.")
        else:
            who = _name(target.display_name, target.telegram_user_id)
            wanted: Literal["approved", "denied"] = "approved" if action == "approve" else "denied"
            if target.status == wanted:
                outbox.add(sender.chat_id, f"{who} is already {wanted}.")
            else:
                await users.set_status(session, target.id, wanted)
                outbox.add(sender.chat_id, f"{wanted.capitalize()} {who}.")
                if wanted == "approved":
                    outbox.add(
                        target.chat_id or target.telegram_user_id,
                        "You're approved. Choose your city with /city, then watch a movie "
                        "with /watch <title>.",
                    )
    await _flush(env, outbox)
