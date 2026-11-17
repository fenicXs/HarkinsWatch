"""Which screen formats a person wants ("2D or MS - Infinity Vision, no 3D") and whether a
label found on a site ("MS INFINITY VISION 3D", "English - 2D") satisfies it.

The rules are plain text matching, so an alert never depends on a model's mood. Only a label
the rules cannot place (for example "IMAX" when the person asked for 2D) is `unknown`, and the
worker may ask the LLM about those once (`llm/tasks/format_label.py`).
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["match", "no", "unknown"]

_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
MAX_ITEMS = 5
MAX_LENGTH = 40


def key(label: str) -> str:
    """Letters and digits only, lower case: 'MS - Infinity Vision' and 'ms infinity vision' agree."""
    return _NOT_ALNUM.sub("", label.casefold())


def _clean(items: object) -> tuple[str, ...]:
    if not isinstance(items, list | tuple):
        return ()
    cleaned = [" ".join(str(i).split())[:MAX_LENGTH] for i in items if isinstance(i, str)]
    return tuple(dict.fromkeys(c for c in cleaned if key(c)))[:MAX_ITEMS]


@dataclass(frozen=True)
class FormatPref:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @classmethod
    def of(cls, include: object = (), exclude: object = ()) -> "FormatPref":
        return cls(_clean(include), _clean(exclude))

    @property
    def empty(self) -> bool:
        return not self.include and not self.exclude

    def to_json(self) -> str | None:
        """What is stored on a watch; None when there is no preference."""
        if self.empty:
            return None
        return json.dumps({"include": list(self.include), "exclude": list(self.exclude)})

    @classmethod
    def from_json(cls, text: str | None) -> "FormatPref":
        if not text:
            return cls()
        try:
            raw = json.loads(text)
        except ValueError:
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls.of(raw.get("include"), raw.get("exclude"))

    def describe(self) -> str:
        parts = []
        if self.include:
            parts.append(" or ".join(self.include))
        if self.exclude:
            parts.append("no " + ", no ".join(self.exclude))
        return "; ".join(parts)


def classify(label: str, pref: FormatPref) -> Verdict:
    """Does a format label satisfy the preference? No preference accepts everything."""
    if pref.empty:
        return "match"
    k = key(label)
    if any(key(x) and key(x) in k for x in pref.exclude):
        return "no"
    if not pref.include:
        return "match"
    wants_3d = any("3d" in key(i) for i in pref.include)
    if "3d" in k and not wants_3d:
        return "no"  # a 3D show never satisfies a preference that did not ask for 3D
    if any(key(i) and key(i) in k for i in pref.include):
        return "match"
    if "2d" in k or "3d" in k:
        return "no"  # a known dimension that is not one they asked for
    return "unknown"
