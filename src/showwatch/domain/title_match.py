"""Title helpers shared by the repositories and (from M2) the matching handlers."""

import re

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """casefold(), punctuation removed, whitespace collapsed (docs/plan.md Appendix A)."""
    return _SPACES.sub(" ", _NON_WORD.sub("", title.casefold())).strip()
