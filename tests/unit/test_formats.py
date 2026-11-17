"""Screen-format preferences: what satisfies "2D or MS - Infinity Vision, no 3D"."""

import pytest

from showwatch.domain.formats import FormatPref, classify

WANT = FormatPref.of(["2D", "MS - Infinity Vision"], ["3D"])


@pytest.mark.parametrize(
    ("label", "verdict"),
    [
        ("2D", "match"),
        ("English - 2D", "match"),
        ("MS - Infinity Vision", "match"),
        ("MS INFINITY VISION", "match"),
        ("ms-infinity vision 2d", "match"),
        ("MS-Infinity Vsn 3d", "no"),
        ("MS INFINITY VISION 3D", "no"),  # contains the wanted name, but it is 3D
        ("3D", "no"),
        ("IMAX 3D", "no"),
        ("IMAX", "unknown"),
        ("DOLBY 7.1", "unknown"),
        ("", "unknown"),
    ],
)
def test_labels_against_a_2d_and_infinity_vision_preference(label: str, verdict: str) -> None:
    assert classify(label, WANT) == verdict


def test_no_preference_accepts_everything() -> None:
    assert classify("MS INFINITY VISION 3D", FormatPref()) == "match"
    assert classify("", FormatPref()) == "match"


def test_an_include_list_without_exclusions_still_keeps_3d_out() -> None:
    assert classify("3D", FormatPref.of(["2D"])) == "no"
    assert classify("IMAX 2D", FormatPref.of(["2D"])) == "match"


def test_asking_for_3d_allows_3d() -> None:
    pref = FormatPref.of(["3D"])
    assert classify("MS INFINITY VISION 3D", pref) == "match"
    assert classify("2D", pref) == "no"


def test_exclusions_alone_only_remove_what_they_name() -> None:
    pref = FormatPref.of([], ["IMAX"])
    assert classify("IMAX 3D", pref) == "no"
    assert classify("3D", pref) == "match"


def test_a_preference_survives_storage_and_odd_input() -> None:
    assert FormatPref.from_json(WANT.to_json()) == WANT
    assert FormatPref().to_json() is None
    assert FormatPref.from_json(None) == FormatPref()
    assert FormatPref.from_json("not json") == FormatPref()
    assert FormatPref.from_json("[1, 2]") == FormatPref()
    messy = FormatPref.of(["  2D ", "2D", "", 7, "x" * 99], "3D")  # type: ignore[arg-type]
    assert messy.include == ("2D", "x" * 40)
    assert messy.exclude == ()  # a bare string is not a list


def test_the_preference_reads_back_in_words() -> None:
    assert WANT.describe() == "2D or MS - Infinity Vision; no 3D"
    assert FormatPref.of(["2D"]).describe() == "2D"
    assert FormatPref.of([], ["3D", "IMAX"]).describe() == "no 3D, no IMAX"
