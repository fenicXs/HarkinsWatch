"""Seat choice on made-up maps (no browser)."""

from showwatch.chains.harkins import seatmap
from showwatch.chains.harkins.seatmap import ConfirmedSeat


def seat(row: str, num: int, x: float | None = None, y: float = 0) -> seatmap.Seat:
    parsed = seatmap.parse_seat(f"{row}{num}", float(num * 20) if x is None else x, y)
    assert parsed is not None
    return parsed


def ids(pairs: list[list[seatmap.Seat]]) -> list[list[str]]:
    return [[s["id"] for s in pair] for pair in pairs]


def test_parse_seat_reads_row_and_number_and_refuses_other_ids() -> None:
    assert seatmap.parse_seat("AB12", 1, 2) == {
        "id": "AB12",
        "row": "AB",
        "num": 12,
        "x": 1,
        "y": 2,
    }
    assert seatmap.parse_seat("12", 1, 2) is None
    assert seatmap.parse_seat("", 1, 2) is None
    assert seatmap.parse_seat(None, 1, 2) is None


def test_fewer_than_three_rows_is_one_region() -> None:
    seats = [seat("A", 1, y=0), seat("A", 2, y=0), seat("B", 1, y=10), seat("B", 2, y=10)]
    assert seatmap.split_regions(seats) == [seats]


def test_an_aisle_splits_back_first_then_front() -> None:
    rows = {"A": 0, "B": 10, "C": 20, "D": 80, "E": 90}  # the big gap is between C and D
    seats = [seat(r, n, y=y) for r, y in rows.items() for n in (1, 2)]
    back, front = seatmap.split_regions(seats)
    assert {s["row"] for s in back} == {"D", "E"}  # larger y = further back
    assert {s["row"] for s in front} == {"A", "B", "C"}


def test_evenly_spaced_rows_are_not_split() -> None:
    seats = [seat(r, 1, y=y) for r, y in zip("ABCD", (0, 10, 20, 30), strict=True)]
    assert len(seatmap.split_regions(seats)) == 1


def test_pairs_are_consecutive_same_row_seats_nearest_the_centre_first() -> None:
    region = [seat("A", n) for n in (1, 2, 3, 4, 5, 6)]
    # The centre x is 70; pairs 3-4 (mid x 70) come first, then 2-3 / 4-5, then the ends.
    assert ids(seatmap.region_pairs(region))[0] == ["A3", "A4"]
    assert ids(seatmap.region_pairs(region))[-1] in (["A1", "A2"], ["A5", "A6"])


def test_a_gap_in_the_numbers_makes_no_pair() -> None:
    assert seatmap.region_pairs([seat("A", 3), seat("A", 5)]) == []
    assert seatmap.region_pairs([]) == []


def test_pairs_never_join_two_rows() -> None:
    assert seatmap.region_pairs([seat("A", 1), seat("B", 2)]) == []


def test_candidates_list_the_back_region_before_the_front() -> None:
    rows = {"A": 0, "B": 10, "C": 20, "D": 80, "E": 90}
    seats = [seat(r, n, y=y) for r, y in rows.items() for n in (1, 2)]
    first_rows = [pair[0]["row"] for pair in seatmap.candidate_pairs(seats)]
    assert set(first_rows[:2]) == {"D", "E"}
    assert set(first_rows[2:]) == {"A", "B", "C"}


def test_best_run_picks_adjacent_seats_nearest_the_middle_of_the_confirmed_seats() -> None:
    confirmed = {
        "A1": ConfirmedSeat("A", 1, 10, 50),
        "A2": ConfirmedSeat("A", 2, 30, 50),
        "A8": ConfirmedSeat("A", 8, 170, 50),
        "A9": ConfirmedSeat("A", 9, 190, 50),
        "A4": ConfirmedSeat("A", 4, 70, 50),
        "A5": ConfirmedSeat("A", 5, 90, 50),
    }
    run = seatmap.best_run(confirmed, 2)
    assert run is not None
    assert [r[0] for r in run] == ["A4", "A5"]


def test_best_run_needs_adjacent_seats_in_one_row() -> None:
    far_apart = {
        "A1": ConfirmedSeat("A", 1, 0, 0),
        "A3": ConfirmedSeat("A", 3, 40, 0),
        "B2": ConfirmedSeat("B", 2, 20, 20),
    }
    assert seatmap.best_run(far_apart, 2) is None
    assert seatmap.best_run({}, 2) is None
    assert seatmap.best_run({"A1": ConfirmedSeat("A", 1, 0, 0)}, 2) is None
