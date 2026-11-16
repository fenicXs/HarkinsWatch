import pytest

from showwatch.domain.title_match import normalize_title


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("DUNE: Part Three", "dune part three"),
        (
            "  Spider-Man:   Un nuevo día [doblado en Español] ",
            "spiderman un nuevo día doblado en español",
        ),
        ("Avengers: Doomsday (4D 3D)", "avengers doomsday 4d 3d"),
        ("Código: Venganza", "código venganza"),
        ("", ""),
    ],
)
def test_normalize_title(title: str, expected: str) -> None:
    assert normalize_title(title) == expected
