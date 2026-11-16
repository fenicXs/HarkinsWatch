"""Job catalogue rules that need no database."""

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from showwatch.db import queue
from showwatch.db.queue import JOB_SPECS, JobSpec, backoff_seconds
from showwatch.db.tables import LIMITED_JOB_KINDS

NO_SESSION = cast(AsyncSession, None)  # the checks below raise before the session is touched


def test_the_catalogue_matches_the_plan() -> None:
    assert {kind: spec.max_attempts for kind, spec in JOB_SPECS.items()} == {
        "check_chain": 3,
        "search_showtimes": 2,
        "book": 1,
        "probe_signals": 3,
    }
    assert {kind for kind, spec in JOB_SPECS.items() if spec.limit} == set(LIMITED_JOB_KINDS)


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(1, 60), (2, 120), (3, 240)],
)
def test_back_off_doubles_each_failed_attempt(attempts: int, expected: int) -> None:
    assert backoff_seconds(JOB_SPECS["check_chain"], attempts) == expected


def test_search_back_off_starts_at_thirty_seconds() -> None:
    assert backoff_seconds(JOB_SPECS["search_showtimes"], 1) == 30


def test_a_job_without_a_back_off_is_never_retried() -> None:
    assert backoff_seconds(JobSpec(1, None, "chain"), 1) is None


@pytest.mark.parametrize(
    ("kind", "kwargs"),
    [
        ("nonsense", {}),
        ("check_chain", {"chain_id": 1}),  # needs a city too
        ("check_chain", {"city_id": 1}),  # needs a chain too
        ("book", {}),  # needs a chain
        ("book", {"chain_id": 1, "city_id": 1}),  # one per chain: no city allowed
        ("probe_signals", {"chain_id": 1, "city_id": 2}),
    ],
)
async def test_enqueue_rejects_jobs_that_break_the_catalogue(
    kind: str, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        await queue.enqueue(NO_SESSION, kind, {}, **kwargs)
