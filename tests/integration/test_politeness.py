"""Back-off against the real chains table."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.chains.base import BlockedError
from showwatch.chains.politeness import BACKOFF_BASE_S, BACKOFF_CAP_S, Politeness
from showwatch.db.repositories import catalog

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _politeness(factory: async_sessionmaker[AsyncSession], clock: _Clock) -> Politeness:
    return Politeness(factory, per_minute=600, now=clock)


async def _blocked_until(factory: async_sessionmaker[AsyncSession], code: str) -> datetime | None:
    async with factory() as session:
        chain = await catalog.get_chain_by_code(session, code)
    assert chain
    return chain.blocked_until


async def test_the_first_block_lasts_five_minutes_then_doubles_up_to_an_hour(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = _Clock()
    politeness = _politeness(session_factory, clock)
    minutes = []
    for _ in range(7):
        until = await politeness.record_blocked("harkins")
        minutes.append((until - clock.now) / timedelta(minutes=1))
    assert minutes == [5, 10, 20, 40, 60, 60, 60]  # capped at an hour
    assert BACKOFF_BASE_S == 300
    assert BACKOFF_CAP_S == 3600


async def test_a_blocked_chain_refuses_requests_until_the_block_ends(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = _Clock()
    politeness = _politeness(session_factory, clock)
    await politeness.before_request("harkins")  # fine at first
    until = await politeness.record_blocked("harkins")
    assert await _blocked_until(session_factory, "harkins") == until

    with pytest.raises(BlockedError, match="backing off"):
        await politeness.before_request("harkins")
    clock.now = until + timedelta(seconds=1)
    await politeness.before_request("harkins")  # the block has ended


async def test_a_block_survives_a_restart(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = _Clock()
    await _politeness(session_factory, clock).record_blocked("harkins")
    restarted = _politeness(session_factory, clock)  # a new process: nothing in memory
    with pytest.raises(BlockedError):
        await restarted.before_request("harkins")


async def test_blocking_one_chain_does_not_stop_another(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    politeness = _politeness(session_factory, _Clock())
    await politeness.record_blocked("harkins")
    await politeness.before_request("bms")


async def test_a_clean_response_clears_the_block_and_the_back_off_level(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = _Clock()
    politeness = _politeness(session_factory, clock)
    await politeness.record_blocked("harkins")
    await politeness.record_blocked("harkins")  # second step: 10 minutes

    await politeness.record_ok("harkins")

    assert await _blocked_until(session_factory, "harkins") is None
    again = await politeness.record_blocked("harkins")
    assert again - clock.now == timedelta(minutes=5)  # back to the first step


async def test_a_clean_response_with_nothing_to_forget_changes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    politeness = _politeness(session_factory, _Clock())
    await politeness.record_ok("harkins")
    assert await _blocked_until(session_factory, "harkins") is None


async def test_blocking_an_unknown_chain_is_an_error(session: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await catalog.set_chain_blocked_until(session, "nowhere", T0)
