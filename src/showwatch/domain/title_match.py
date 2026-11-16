"""Title helpers shared by the repositories and (from M2) the matching handlers."""

import re
from collections.abc import Sequence
from difflib import get_close_matches

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """casefold(), punctuation removed, whitespace collapsed (docs/plan.md Appendix A)."""
    return _SPACES.sub(" ", _NON_WORD.sub("", title.casefold())).strip()


def _match_key(text: str) -> str:
    """For comparing what a user typed with a title: casefolded, punctuation read as a space."""
    return _SPACES.sub(" ", _NON_WORD.sub(" ", text.casefold())).strip()


def find_matches(query: str, titles: Sequence[str]) -> list[str]:
    """Titles that match what a user typed: at most 5, in this order of preference.

    Port of the legacy bot's matcher, with one improvement: case, punctuation and extra spaces
    are ignored on both sides ("dune!" finds "DUNE: Part Three"). Substring matches win; only
    when there are none, close spellings (`difflib`, cutoff 0.3) are offered, so the two never
    mix.
    """
    needle = _match_key(query)
    if not needle:
        return []
    title_by_key: dict[str, str] = {}
    for title in titles:
        title_by_key.setdefault(_match_key(title), title)
    matches = [key for key in title_by_key if needle in key]
    if not matches:
        matches = get_close_matches(needle, list(title_by_key), n=5, cutoff=0.3)
    return [title_by_key[key] for key in matches[:5]]
