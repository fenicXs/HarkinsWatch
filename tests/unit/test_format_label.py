"""The LLM fallback for screen-format labels the plain rules cannot place."""

from llmfakes import FakeLLM
from showwatch.domain.formats import FormatPref
from showwatch.llm.tasks import format_label

PREF = FormatPref.of(["2D"], ["3D"])


def test_numbers_must_be_in_range() -> None:
    assert format_label.parse_numbers("[1, 5, 0, true]", 2) == {1}
    assert format_label.parse_numbers("nothing", 2) == set()
    assert format_label.parse_numbers("[x]", 2) == set()


async def test_labels_are_judged_and_remembered() -> None:
    format_label._CACHE.clear()
    llm = FakeLLM(labels_reply="[2]")

    first = await format_label.judge(llm, ["IMAX", "LASER"], PREF)
    again = await format_label.judge(llm, ["IMAX", "LASER"], PREF)

    assert first == again == {"IMAX": False, "LASER": True}
    assert len(llm.calls) == 1  # the second time came from memory


async def test_a_model_failure_judges_nothing_and_is_not_remembered() -> None:
    format_label._CACHE.clear()
    assert await format_label.judge(FakeLLM(fail=True), ["IMAX"], PREF) == {"IMAX": False}
    assert await format_label.judge(FakeLLM(labels_reply="[1]"), ["IMAX"], PREF) == {"IMAX": True}
