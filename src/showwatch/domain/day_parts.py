"""Parts of the day, in the theatre's local time (docs/plan.md, M6)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from showwatch.domain.models import PartOfDay

# [start hour, end hour) in local time. "night" wraps past midnight: 21:00 to 02:00.
WINDOWS: dict[PartOfDay, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 2),
}


def in_part_of_day(starts_at: datetime, part: PartOfDay, tz_name: str) -> bool:
    hour = starts_at.astimezone(ZoneInfo(tz_name)).hour
    start, end = WINDOWS[part]
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight
