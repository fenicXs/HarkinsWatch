"""Title helpers shared by the repositories and (from M2) the matching handlers."""

import re
from collections.abc import Sequence
from difflib import get_close_matches

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """casefold(), punctuation removed, whitespace collapsed (docs/plan.md Appendix A)."""
    return _SPACES.sub(" ", _NON_WORD.sub("", title.casefold())).strip()


def find_matches(query: str, titles: Sequence[str]) -> list[str]:
    """Titles that match what a user typed: at most 5, in this order of preference.

    Port of the legacy bot's matcher. Lowercase substring matches win; only when there are
    none, close spellings (`difflib`, cutoff 0.3) are offered, so the two never mix.
    """
    needle = query.strip().lower()
    if not needle:
        return []
    title_by_lower = {title.lower(): title for title in titles}
    matches = [low for low in title_by_lower if needle in low]
    if not matches:
        matches = get_close_matches(needle, list(title_by_lower), n=5, cutoff=0.3)
    return [title_by_lower[low] for low in matches[:5]]
