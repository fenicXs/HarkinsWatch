"""The per-chain request budget, with a fake clock so no test really waits."""

from showwatch.chains.politeness import RequestBudget


class _FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _budget(fake: _FakeTime, per_minute: int = 10) -> RequestBudget:
    return RequestBudget(per_minute, clock=fake.clock, sleep=fake.sleep)


async def test_a_full_budget_is_spent_without_waiting() -> None:
    fake = _FakeTime()
    budget = _budget(fake)
    for _ in range(10):
        await budget.acquire("harkins")
    assert fake.slept == []


async def test_the_eleventh_request_in_a_minute_waits() -> None:
    fake = _FakeTime()
    budget = _budget(fake)
    for _ in range(10):
        await budget.acquire("harkins")
    await budget.acquire("harkins")
    assert fake.slept == [6.0]  # one token per 6 seconds at 10 per minute


async def test_the_budget_refills_over_time() -> None:
    fake = _FakeTime()
    budget = _budget(fake)
    for _ in range(10):
        await budget.acquire("harkins")
    fake.now += 60  # a minute passes
    for _ in range(10):
        await budget.acquire("harkins")
    assert fake.slept == []


async def test_each_chain_has_its_own_budget() -> None:
    fake = _FakeTime()
    budget = _budget(fake)
    for _ in range(10):
        await budget.acquire("harkins")
    await budget.acquire("bms")  # a different chain is not held up
    assert fake.slept == []


async def test_the_budget_never_banks_more_than_a_full_bucket() -> None:
    fake = _FakeTime()
    budget = _budget(fake, per_minute=5)
    await budget.acquire("harkins")
    fake.now += 3600  # an idle hour must not allow a burst of 60
    for _ in range(5):
        await budget.acquire("harkins")
    await budget.acquire("harkins")
    assert len(fake.slept) == 1
