"""Be a polite visitor: a request budget per chain, and back-off when a site pushes back.

- Budget: a token bucket per chain (`CHAIN_REQUEST_BUDGET_PER_MIN`, in memory).
- Back-off: after a 403, a 429 or a challenge page the chain is blocked for 5 minutes, doubling
  on each repeat up to 60 minutes, and reset after one clean response. The block is stored in
  `chains.blocked_until`, so it survives a restart (the doubling level does not).

This never works around a block: no retries from another address, no solving challenges.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from showwatch.chains.base import BlockedError
from showwatch.db.repositories import catalog

BACKOFF_BASE_S = 300  # 5 minutes
BACKOFF_CAP_S = 3600  # 60 minutes

log = structlog.get_logger(__name__)


class RequestBudget:
    """Token bucket: `per_minute` requests per minute, spent one per request."""

    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0  # tokens per second
        self._clock = clock
        self._sleep = sleep
        self._tokens: dict[str, float] = {}
        self._updated: dict[str, float] = {}

    async def acquire(self, chain_code: str) -> None:
        """Wait until a request to this chain is allowed."""
        while True:
            now = self._clock()
            tokens = self._tokens.get(chain_code, self._capacity)
            tokens = min(
                self._capacity, tokens + (now - self._updated.get(chain_code, now)) * self._rate
            )
            self._updated[chain_code] = now
            if tokens >= 1.0:
                self._tokens[chain_code] = tokens - 1.0
                return
            self._tokens[chain_code] = tokens
            await self._sleep((1.0 - tokens) / self._rate)


class Politeness:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        per_minute: int,
        *,
        budget: RequestBudget | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._budget = budget or RequestBudget(per_minute)
        self._now = now
        self._level: dict[str, int] = {}

    async def before_request(self, chain_code: str) -> None:
        """Call before every request: refuses while blocked, then waits for budget."""
        async with self._session_factory() as session:
            chain = await catalog.get_chain_by_code(session, chain_code)
        if chain is not None and chain.blocked_until and chain.blocked_until > self._now():
            raise BlockedError(
                f"{chain_code} is backing off until {chain.blocked_until.isoformat()}"
            )
        await self._budget.acquire(chain_code)

    async def record_blocked(self, chain_code: str) -> datetime:
        """The site pushed back: block the chain for the next back-off step. Returns the end."""
        level = self._level.get(chain_code, 0)
        delay = min(BACKOFF_BASE_S * (1 << level), BACKOFF_CAP_S)
        self._level[chain_code] = level + 1
        until = self._now() + timedelta(seconds=delay)
        async with self._session_factory() as session, session.begin():
            await catalog.set_chain_blocked_until(session, chain_code, until)
        log.warning("chain_blocked", chain=chain_code, backoff_s=delay, until=until.isoformat())
        return until

    async def record_ok(self, chain_code: str) -> None:
        """A clean response: forget the back-off level."""
        if self._level.pop(chain_code, None) is not None:
            async with self._session_factory() as session, session.begin():
                await catalog.set_chain_blocked_until(session, chain_code, None)
