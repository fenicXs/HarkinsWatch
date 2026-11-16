"""Who may use the bot: the gate, /start, and the admin's approve/deny buttons."""

import pytest

from conftest import ADMIN_ID
from showwatch.bot.handlers import Env, Sender, access
from showwatch.db.repositories import users
from telegramfakes import FakeNotifier


def _sender(telegram_user_id: int, name: str | None = "Ann") -> Sender:
    return Sender(telegram_user_id=telegram_user_id, chat_id=telegram_user_id, display_name=name)


async def _get(env: Env, telegram_user_id: int):  # type: ignore[no-untyped-def]
    async with env.session_factory() as session:
        return await users.get_by_telegram_id(session, telegram_user_id)


async def test_the_admin_exists_approved_after_startup(env: Env) -> None:
    admin = await _get(env, ADMIN_ID)
    assert admin is not None
    assert (admin.status, admin.is_admin, admin.chat_id) == ("approved", True, None)


async def test_the_admin_gets_through_and_their_chat_id_is_learned(env: Env) -> None:
    assert await access.gate(env, _sender(ADMIN_ID, "Root")) == "allow"
    admin = await _get(env, ADMIN_ID)
    assert admin is not None
    assert admin.chat_id == ADMIN_ID


async def test_a_stranger_is_recorded_as_pending_and_the_admin_is_asked(
    env: Env, notifier: FakeNotifier
) -> None:
    assert await access.gate(env, _sender(111, "Ann")) == "stop"

    stranger = await _get(env, 111)
    assert stranger is not None
    assert (stranger.status, stranger.pending_notified, stranger.is_admin) == (
        "pending",
        True,
        False,
    )
    assert notifier.to(111) == [access.ACCESS_REQUESTED]
    assert len(notifier.to(ADMIN_ID)) == 1
    assert "Ann" in notifier.to(ADMIN_ID)[0]
    assert notifier.buttons_to(ADMIN_ID) == [
        ("Approve", f"approve:{stranger.id}"),
        ("Deny", f"deny:{stranger.id}"),
    ]


async def test_a_pending_user_gets_one_reply_and_silence_after(
    env: Env, notifier: FakeNotifier
) -> None:
    for _ in range(3):
        assert await access.gate(env, _sender(111)) == "stop"
    assert notifier.to(111) == [access.ACCESS_REQUESTED]  # once
    assert len(notifier.to(ADMIN_ID)) == 1  # the admin was asked once


async def test_a_pending_user_who_was_never_told_is_told_once_without_asking_the_admin_again(
    env: Env, notifier: FakeNotifier
) -> None:
    async with env.session_factory() as session, session.begin():
        await users.create_pending(session, telegram_user_id=222, chat_id=222, display_name="Bo")
    assert await access.gate(env, _sender(222, "Bo")) == "stop"
    assert notifier.to(222) == [access.ACCESS_REQUESTED]
    assert notifier.to(ADMIN_ID) == []


async def test_approving_lets_the_user_in_and_tells_both_people(
    env: Env, notifier: FakeNotifier
) -> None:
    await access.gate(env, _sender(111, "Ann"))
    ann = await _get(env, 111)
    assert ann is not None
    notifier.sent.clear()

    await access.handle_decision(env, _sender(ADMIN_ID, "Root"), "approve", ann.id)

    assert (await _get(env, 111)).status == "approved"  # type: ignore[union-attr]
    assert notifier.to(ADMIN_ID) == ["Approved Ann."]
    assert "approved" in notifier.to(111)[0].lower()
    assert await access.gate(env, _sender(111, "Ann")) == "allow"


async def test_denying_keeps_the_user_out_and_in_silence(env: Env, notifier: FakeNotifier) -> None:
    await access.gate(env, _sender(111, "Ann"))
    ann = await _get(env, 111)
    assert ann is not None
    notifier.sent.clear()

    await access.handle_decision(env, _sender(ADMIN_ID), "deny", ann.id)

    assert (await _get(env, 111)).status == "denied"  # type: ignore[union-attr]
    assert notifier.to(ADMIN_ID) == ["Denied Ann."]
    assert notifier.to(111) == []  # the user is not told
    for _ in range(2):
        assert await access.gate(env, _sender(111)) == "stop"
    assert notifier.to(111) == []  # and stays in silence


async def test_only_the_admin_can_approve(env: Env, notifier: FakeNotifier) -> None:
    await access.gate(env, _sender(111, "Ann"))
    await access.gate(env, _sender(222, "Bo"))
    ann = await _get(env, 111)
    assert ann is not None
    notifier.sent.clear()

    await access.handle_decision(env, _sender(222, "Bo"), "approve", ann.id)  # Bo is not the admin

    assert (await _get(env, 111)).status == "pending"  # type: ignore[union-attr]
    assert notifier.sent == []


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [("approve", "approve", "is already approved"), ("deny", "deny", "is already denied")],
)
async def test_repeating_a_decision_says_so(
    env: Env, notifier: FakeNotifier, first: str, second: str, expected: str
) -> None:
    await access.gate(env, _sender(111, "Ann"))
    ann = await _get(env, 111)
    assert ann is not None
    await access.handle_decision(env, _sender(ADMIN_ID), first, ann.id)  # type: ignore[arg-type]
    notifier.sent.clear()
    await access.handle_decision(env, _sender(ADMIN_ID), second, ann.id)  # type: ignore[arg-type]
    assert expected in notifier.to(ADMIN_ID)[0]


async def test_a_decision_about_a_missing_user_is_reported(
    env: Env, notifier: FakeNotifier
) -> None:
    await access.handle_decision(env, _sender(ADMIN_ID), "approve", 999_999)
    assert notifier.to(ADMIN_ID) == ["That user no longer exists."]


async def test_the_admin_can_change_their_mind_about_a_denied_user(
    env: Env, notifier: FakeNotifier
) -> None:
    await access.gate(env, _sender(111, "Ann"))
    ann = await _get(env, 111)
    assert ann is not None
    await access.handle_decision(env, _sender(ADMIN_ID), "deny", ann.id)
    await access.handle_decision(env, _sender(ADMIN_ID), "approve", ann.id)
    assert await access.gate(env, _sender(111)) == "allow"


async def test_start_welcomes_an_approved_user(env: Env, notifier: FakeNotifier) -> None:
    await access.handle_start(env, _sender(ADMIN_ID))
    assert "You're set up" in notifier.to(ADMIN_ID)[0]
    assert "/city" in notifier.to(ADMIN_ID)[0]


async def test_an_unnamed_user_is_described_by_their_id(env: Env, notifier: FakeNotifier) -> None:
    await access.gate(env, Sender(telegram_user_id=333, chat_id=333, display_name=None))
    assert "user 333" in notifier.to(ADMIN_ID)[0]
