"""Tests for the red-flag transcript matcher and its phrase sets."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import redflags
from scripts.redflag_eval import Item, SetError, load_items, run, summary

SETS_DIR = Path(__file__).resolve().parent / "redflags"
TUNING = SETS_DIR / "tuning.yaml"
HOLDOUT = SETS_DIR / "holdout.yaml"

TUNING_ITEMS = load_items(TUNING)


def fed(*turns: str) -> set[str]:
    tracker = redflags.RedFlagTracker()
    found: set[str] = set()
    for turn in turns:
        found.update(tracker.feed(turn))
    return found


@pytest.mark.parametrize("item", TUNING_ITEMS, ids=lambda item: item.item_id)
def test_tuning_item_gives_exactly_its_expected_result(item: Item) -> None:
    assert fed(*item.turns) == set(item.expect)


def test_tuning_set_shape() -> None:
    """The set is big enough and balanced enough to tune on (contracts §6.2)."""
    assert len(TUNING_ITEMS) >= 160
    per_lang = {lang: [i for i in TUNING_ITEMS if i.lang == lang] for lang in ("es", "en")}
    for items in per_lang.values():
        assert len(items) >= len(TUNING_ITEMS) // 3
    for reason in redflags.REASONS:
        assert sum(1 for i in TUNING_ITEMS if reason in i.expect) >= 20
    assert sum(1 for i in TUNING_ITEMS if not i.expect) >= 70
    assert sum(1 for i in TUNING_ITEMS if len(i.turns) == 2) >= 10


def test_tuning_set_measures_perfectly() -> None:
    data = summary(run(TUNING_ITEMS))
    assert data["recall"]["share"] == 1.0
    assert data["exact_recall"]["share"] == 1.0
    assert data["false_alarms"]["share"] == 0.0


@pytest.mark.skipif(not HOLDOUT.exists(), reason="no holdout set yet")
def test_holdout_set_format_only() -> None:
    """The holdout set is measured by the script, never asserted on here."""
    assert load_items(HOLDOUT)


def test_normalise_drops_accents_case_and_punctuation() -> None:
    assert redflags.normalise("¡Me CUESTA respirar!") == "me cuesta respirar"
    assert redflags.normalise("hinchazón, fiebre… 38,5") == "hinchazon fiebre 38 5"
    assert redflags.normalise("can't  breathe\n") == "can t breathe"
    assert redflags.normalise("NIÑO  —  mandíbula") == "nino mandibula"


def test_accents_never_matter() -> None:
    assert redflags.match("me he caido y me he partido un diente") == ["red_flag_trauma"]
    assert redflags.match("me he caído y me he partido un diente") == ["red_flag_trauma"]


def test_both_languages_are_always_checked() -> None:
    """Callers switch mid-sentence, so the call's language never gates the lists."""
    assert redflags.match("my face is swollen y tengo 39 de fiebre") == ["red_flag_swelling_fever"]
    assert redflags.match("doctor, i can't breathe, me falta el aire") == ["red_flag_breathing"]


def test_swelling_then_fever_across_turns() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("tengo la cara hinchada") == []
    assert tracker.feed("y ahora tengo fiebre") == ["red_flag_swelling_fever"]


def test_fever_then_swelling_across_turns() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("i have a fever since yesterday") == []
    assert tracker.feed("and my cheek is swollen now") == ["red_flag_swelling_fever"]


def test_tracker_is_per_call() -> None:
    first = redflags.RedFlagTracker()
    assert first.feed("tengo la cara hinchada") == []
    second = redflags.RedFlagTracker()
    assert second.feed("y ahora tengo fiebre") == []


def test_reasons_come_back_in_priority_order_without_duplicates() -> None:
    text = (
        "no puedo respirar, tengo la cara hinchada y 39 de fiebre, "
        "me he dado un golpe en la boca y la encia no para de sangrar"
    )
    reasons = redflags.match(text)
    assert reasons == list(redflags.REASONS)
    assert len(reasons) == len(set(reasons))


def test_match_is_pure_and_stateless() -> None:
    assert redflags.match("tengo la cara hinchada") == []
    assert redflags.match("y ahora tengo fiebre") == []
    assert redflags.match("") == []


def test_plain_negations_do_not_fire() -> None:
    assert redflags.match("tengo la encia hinchada pero no tengo fiebre") == []
    assert redflags.match("my gum is swollen, no fever") == []
    assert redflags.match("the socket is not bleeding any more") == []
    assert redflags.match("me sacaron la muela y ya no sangra") == []


def test_set_validation_rejects_bad_files(tmp_path: Path) -> None:
    def write(body: str) -> Path:
        path = tmp_path / "set.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    with pytest.raises(SetError, match="unknown labels"):
        load_items(
            write(
                "version: 1\nitems:\n"
                "  - {id: es-001, lang: es, turns: ['hola'], expect: [red_flag_cold]}\n"
            )
        )
    with pytest.raises(SetError, match="duplicate id"):
        load_items(
            write(
                "version: 1\nitems:\n"
                "  - {id: es-001, lang: es, turns: ['hola'], expect: []}\n"
                "  - {id: es-001, lang: es, turns: ['adios'], expect: []}\n"
            )
        )
    with pytest.raises(SetError, match="bad id"):
        load_items(
            write("version: 1\nitems:\n  - {id: es1, lang: es, turns: ['hola'], expect: []}\n")
        )
    with pytest.raises(SetError, match="turns must be"):
        load_items(
            write(
                "version: 1\nitems:\n"
                "  - {id: en-001, lang: en, turns: ['a', 'b', 'c'], expect: []}\n"
            )
        )
    with pytest.raises(SetError, match="bad lang"):
        load_items(
            write("version: 1\nitems:\n  - {id: es-001, lang: fr, turns: ['hola'], expect: []}\n")
        )
    with pytest.raises(SetError, match="items"):
        load_items(write("version: 1\n"))


def test_module_records_that_a_dentist_must_validate_the_list() -> None:
    doc = redflags.__doc__ or ""
    assert "without clinical review" in doc
    assert "licensed dentist" in doc
