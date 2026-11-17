"""Parts of the day in the theatre's local time."""

from datetime import UTC, datetime, timedelta

import pytest

from showwatch.domain.day_parts import in_part_of_day
from showwatch.domain.models import PartOfDay

PHOENIX = "America/Phoenix"  # UTC-7, no daylight saving
PARTS: tuple[PartOfDay, ...] = ("morning", "afternoon", "evening", "night")


def phoenix_hour(hour: int) -> datetime:
    """The instant that is `hour`:00 on Nov 17 in Phoenix."""
    return datetime(2026, 11, 17, 0, 0, tzinfo=UTC) + timedelta(hours=hour + 7)


@pytest.mark.parametrize(
    ("hour", "part"),
    [
        (5, None),
        (6, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (20, "evening"),
        (21, "night"),
        (23, "night"),
        (0, "night"),
        (1, "night"),
        (2, None),
    ],
)
def test_each_hour_falls_in_one_part(hour: int, part: PartOfDay | None) -> None:
    found = {p for p in PARTS if in_part_of_day(phoenix_hour(hour), p, PHOENIX)}
    assert found == ({part} if part else set())


def test_the_zone_is_the_theatres_not_utc() -> None:
    instant = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)  # 19:30 in Bengaluru, 07:00 in Phoenix
    assert in_part_of_day(instant, "evening", "Asia/Kolkata")
    assert in_part_of_day(instant, "morning", PHOENIX)
