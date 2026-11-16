from showwatch.domain.title_match import find_matches

TITLES = [
    "DUNE: Part Three",
    "DUNE: Part Three (IMAX 70mm)",
    "Moana",
    "Spider-Man: Un nuevo día [doblado en Español]",
    "Avengers: Doomsday",
    "Avengers: Doomsday (4D 3D)",
]


def test_substring_matches_are_case_insensitive_and_keep_the_original_title() -> None:
    assert find_matches("dune", TITLES) == ["DUNE: Part Three", "DUNE: Part Three (IMAX 70mm)"]
    assert find_matches("  MOANA ", TITLES) == ["Moana"]


def test_close_spellings_are_offered_only_when_nothing_matches_exactly() -> None:
    # "moanna" is not a substring of anything, so the fuzzy path runs and finds "Moana".
    assert find_matches("moanna", TITLES)[0] == "Moana"


def test_substring_hits_and_fuzzy_hits_never_mix() -> None:
    # "avengers" matches two titles exactly; fuzzy matching must not add "Moana" or others.
    assert find_matches("avengers", TITLES) == ["Avengers: Doomsday", "Avengers: Doomsday (4D 3D)"]


def test_at_most_five_results() -> None:
    many = [f"Movie {n}" for n in range(12)]
    assert len(find_matches("movie", many)) == 5


def test_nothing_close_gives_no_results() -> None:
    assert find_matches("zzzzzzzzzz", TITLES) == []


def test_blank_queries_and_empty_title_lists_give_no_results() -> None:
    assert find_matches("   ", TITLES) == []
    assert find_matches("dune", []) == []


def test_punctuation_and_spacing_are_ignored_on_both_sides() -> None:
    assert find_matches("dune!", TITLES) == ["DUNE: Part Three", "DUNE: Part Three (IMAX 70mm)"]
    assert find_matches("dune   part", TITLES)[0] == "DUNE: Part Three"
    assert find_matches("spider man", TITLES) == [TITLES[3]]
    assert find_matches("Spider-Man: Un nuevo", TITLES) == [TITLES[3]]
    assert find_matches("!!!", TITLES) == []
