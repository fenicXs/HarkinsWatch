"""Small HTML readers shared by the watch-only chains (no browser needed: they read saved HTML)."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

CLOCK = re.compile(r"\b\d{1,2}:\d{2}\s?[AP]M\b", re.IGNORECASE)


@dataclass
class Anchor:
    href: str
    text: str = ""
    labels: list[str] = field(default_factory=list)  # data-content, img alt, aria-label inside


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self.texts: list[str] = []
        self._open: Anchor | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a" and values.get("href"):
            self._open = Anchor(href=values["href"])
            self.anchors.append(self._open)
            label = values.get("alt") or values.get("aria-label")
            if label:
                self._open.labels.append(label)
        elif self._open is not None:
            label = values.get("data-content") or (values.get("alt") if tag == "img" else "")
            if label:
                self._open.labels.append(label)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag == "a":
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            return
        self.texts.append(data.strip())
        if self._open is not None:
            self._open.text = (self._open.text + " " + data.strip()).strip()


def read_page(html: str) -> tuple[list[Anchor], list[str]]:
    """(links with their labels, visible text pieces) of a saved page."""
    collector = _Collector()
    collector.feed(html)
    return collector.anchors, collector.texts


def clock_times(texts: list[str]) -> int:
    """How many 'hh:mm AM/PM' show times appear in the visible text."""
    return sum(len(CLOCK.findall(text)) for text in texts)
