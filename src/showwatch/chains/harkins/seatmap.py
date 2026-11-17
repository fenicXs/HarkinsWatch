"""Choosing seats from a Harkins seat map. Pure functions, no browser.

A seat is a dict with `id` (like "I6"), `row` ("I"), `num` (6), and `x`, `y` (position on the
map; a larger `y` is further back). Ported unchanged from `legacy/booker.py` and `legacy/seat_ai.py`.
"""

import re
from dataclasses import dataclass
from typing import Any

Seat = dict[str, Any]

SEAT_ID = re.compile(r"([A-Z]+)(\d+)")
# A row gap this many times the median gap counts as an aisle between a back and a front block.
AISLE_GAP_FACTOR = 1.6


def parse_seat(raw_id: str | None, x: float, y: float) -> Seat | None:
    """A seat dict from a button's id and position, or None if the id is not "<letters><number>"."""
    match = SEAT_ID.match(raw_id or "")
    if match is None:
        return None
    return {
        "id": raw_id,
        "row": match.group(1),
        "num": int(match.group(2)),
        "x": x,
        "y": y,
    }


def split_regions(seats: list[Seat]) -> list[list[Seat]]:
    """Split into [back, front] by the aisle (largest row gap); one region if there is no clear split."""
    ys = sorted({s["y"] for s in seats})
    if len(ys) < 3:
        return [seats]
    gaps = [(ys[i + 1] - ys[i], ys[i]) for i in range(len(ys) - 1)]
    max_gap, split_after = max(gaps)
    median_gap = sorted(g[0] for g in gaps)[len(gaps) // 2]
    if max_gap > median_gap * AISLE_GAP_FACTOR:  # a real aisle separation
        back = [s for s in seats if s["y"] > split_after]
        front = [s for s in seats if s["y"] <= split_after]
        return [back, front]  # priority 1 = back, priority 2 = front
    return [seats]


def region_pairs(region: list[Seat]) -> list[list[Seat]]:
    """Consecutive same-row pairs, nearest to the region's centre first."""
    if not region:
        return []
    cx = sum(s["x"] for s in region) / len(region)
    cy = sum(s["y"] for s in region) / len(region)
    by_row: dict[str, list[Seat]] = {}
    for s in region:
        by_row.setdefault(s["row"], []).append(s)
    ranked: list[tuple[float, list[Seat]]] = []
    for row_seats in by_row.values():
        row_seats.sort(key=lambda s: s["num"])
        for a, b in zip(row_seats, row_seats[1:], strict=False):
            if b["num"] == a["num"] + 1:
                mx = (a["x"] + b["x"]) / 2
                dist = ((mx - cx) ** 2 + (a["y"] - cy) ** 2) ** 0.5
                ranked.append((dist, [a, b]))
    ranked.sort(key=lambda t: t[0])
    return [pair for _, pair in ranked]


def candidate_pairs(seats: list[Seat]) -> list[list[Seat]]:
    """Every pair: back region first (centre out), then the front region."""
    pairs: list[list[Seat]] = []
    for region in split_regions(seats):
        pairs.extend(region_pairs(region))
    return pairs


@dataclass(frozen=True)
class ConfirmedSeat:
    """A seat the vision step proved real by clicking it: where we clicked, and what it selected."""

    row: str
    num: int
    x: float
    y: float


def best_run(
    confirmed: dict[str, ConfirmedSeat], count: int
) -> list[tuple[str, float, float]] | None:
    """The `count` consecutive same-row confirmed seats nearest the centre of all confirmed seats.

    Returns [(seat id, click x, click y), ...] left to right, or None if no run exists.
    """
    if len(confirmed) < count:
        return None
    cx = sum(v.x for v in confirmed.values()) / len(confirmed)
    cy = sum(v.y for v in confirmed.values()) / len(confirmed)
    best: list[tuple[str, float, float]] | None = None
    best_dist = float("inf")
    for seat_id, first in confirmed.items():
        run = [(seat_id, first.x, first.y)]
        num = first.num
        while len(run) < count:
            nxt = next(
                (s for s, v in confirmed.items() if v.row == first.row and v.num == num + 1), None
            )
            if nxt is None:
                break
            run.append((nxt, confirmed[nxt].x, confirmed[nxt].y))
            num += 1
        if len(run) == count:
            mx = sum(p[1] for p in run) / count
            my = sum(p[2] for p in run) / count
            dist = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist, best = dist, run
    return best
