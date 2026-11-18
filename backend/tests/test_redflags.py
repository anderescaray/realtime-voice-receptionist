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


def test_bleeding_then_wont_stop_across_turns() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("my gums started bleeding earlier") == []
    assert tracker.feed("it's been an hour and it still hasn't stopped") == ["red_flag_bleeding"]
    es_tracker = redflags.RedFlagTracker()
    assert es_tracker.feed("me empezo a salir sangre de la herida") == []
    assert es_tracker.feed("y llevo una hora apretando pero no se corta") == ["red_flag_bleeding"]


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


@pytest.mark.parametrize(
    "text",
    [
        "I didn't fall, someone punched me in the mouth and my tooth is loose",
        "no me cai, me pegaron un punetazo en la boca y tengo el diente suelto",
        "No, I had a fall and broke my front tooth",
        "he was hit in the mouth by accident and his lip is bleeding",
        "le dio un codazo en la boca por accidente y le sangra",
        "years ago I broke my nose, and now I fell and my tooth is out",
        "hace una semana me pusieron un empaste y hoy me he caido y me he roto la paleta",
        "he fell off his bike, his crown came off and his lip is bleeding",
    ],
)
def test_a_denial_or_past_event_never_hides_a_real_blow(text: str) -> None:
    """Cancels remove only their own words; `past` cancels only their own clause."""
    assert "red_flag_trauma" in redflags.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "no me ha dejado de sangrar en toda la tarde",
        "nunca ha dejado de sangrar la herida",
        "it never really stopped bleeding since the extraction",
        "la encia no sangra pero el hueco de la muela no para de sangrar",
        "it stopped bleeding earlier and now it started bleeding again and won't stop",
    ],
)
def test_a_negated_or_partial_stop_still_fires_bleeding(text: str) -> None:
    assert "red_flag_bleeding" in redflags.match(text)


def test_other_cancels_stay_local() -> None:
    assert redflags.match("i have bad breath and now i cant breathe properly") == [
        "red_flag_breathing"
    ]
    assert redflags.match("she isn't breathing normally and her throat is closing") == [
        "red_flag_breathing"
    ]
    assert redflags.match(
        "my tooth is sensitive to cold and I have a fever and my face is swollen"
    ) == ["red_flag_swelling_fever"]


@pytest.mark.parametrize(
    "text",
    [
        "es una urgencia, me duele mucho la muela",
        "comiendo una hogaza de pan se me rompio una muela",
        "a partir de las diez me viene bien",
        "I have a massive toothache",
        "I chipped a tooth eating an artichoke",
        "tomo anticoagulantes y me duele la muela",
        "i can't stop crying, my tooth hurts so much",
        "i fell asleep with a toothache",
    ],
)
def test_words_inside_other_words_do_not_fire(text: str) -> None:
    assert redflags.match(text) == []


def test_blow_then_injury_across_turns() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("my son fell off the swing") == []
    assert tracker.feed("his front tooth is loose now") == ["red_flag_trauma"]
    es_tracker = redflags.RedFlagTracker()
    assert es_tracker.feed("me he caido por las escaleras") == []
    assert es_tracker.feed("y tengo un diente roto") == ["red_flag_trauma"]


def test_an_ordinary_later_turn_does_not_fire_again() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("tengo la cara hinchada y 38 de fiebre") == ["red_flag_swelling_fever"]
    assert tracker.feed("si, gracias") == []
    assert tracker.feed("me llamo Ana") == []


def test_a_later_cancel_clears_what_was_remembered() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("me sangran un poco las encias") == []
    assert tracker.feed("ya ha parado de sangrar, pero llevo una hora con dolor") == []
    swelling = redflags.RedFlagTracker()
    assert swelling.feed("tengo la cara hinchada") == []
    assert swelling.feed("no lo tengo nada hinchado, solo tengo decimas") == []


@pytest.mark.parametrize(
    "text",
    [
        "el niño se cayó en el parque y tiene el diente roto",
        "mi hermano se cayó y se ha roto un diente",
        "bueno, se ha caído y tiene el diente roto",
        "my son fell off his bike and broke his front tooth, same thing happened two years ago",
        "Hace un mes vine a una limpieza y me he dado un golpe en la boca hoy",
        "I broke my crown when I fell off my bike",
        "the ball hit me when I was in goal and my tooth is loose",
    ],
)
def test_cancels_start_at_a_word_and_past_stays_in_its_clause(text: str) -> None:
    assert "red_flag_trauma" in redflags.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "My cheek is swollen. No, I don't have a fever.",
        "Tengo la cara hinchada. No, fiebre no tengo.",
        "No, se me ha caído un empaste y me duele la muela",
        "No, hace diez años me di un golpe en la boca",
        "me dio un pinchazo en la muela al tomar algo frio",
        "tengo 38 años y me duele la muela",
    ],
)
def test_a_no_before_a_denial_or_another_sense_does_not_void_it(text: str) -> None:
    assert redflags.match(text) == []


def test_a_stop_before_a_fresh_wont_stop_still_fires() -> None:
    assert redflags.match(
        "it stopped bleeding for a bit but now it's pouring out and won't stop"
    ) == ["red_flag_bleeding"]
    assert redflags.match("it hasnt stopped bleeding") == ["red_flag_bleeding"]


def test_trauma_parts_in_either_order_and_a_denied_knock_is_not_forgotten() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("my son broke his front tooth and his lip is bleeding") == []
    assert tracker.feed("he tripped and fell in the playground") == ["red_flag_trauma"]
    denied = redflags.RedFlagTracker()
    assert denied.feed("my son fell off the swing") == []
    assert denied.feed("he didn't hit his head but his front tooth is loose") == ["red_flag_trauma"]


def test_a_duration_alone_in_a_later_turn_does_not_complete_bleeding() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("my gums bleed when I brush, I'd like a cleaning") == []
    assert tracker.feed("can I come Friday? I'm at work all day Thursday") == []


# --- review round 4: round-3 findings ---------------------------------------


def test_a_fired_combined_flag_does_not_fire_again_on_later_turns() -> None:
    tracker = redflags.RedFlagTracker()
    assert tracker.feed("me he caído por las escaleras y me he roto un diente") == [
        "red_flag_trauma"
    ]
    assert tracker.feed("¿me pueden dar cita hoy para que me miren el diente?") == []
    assert tracker.feed("no tengo fiebre, solo me duele el diente") == []
    swelling = redflags.RedFlagTracker()
    assert swelling.feed("tengo la cara hinchada y fiebre") == ["red_flag_swelling_fever"]
    assert swelling.feed("la hinchazón es en el lado izquierdo") == []
    bleeding = redflags.RedFlagTracker()
    assert bleeding.feed("the socket is bleeding and it won't stop") == ["red_flag_bleeding"]
    assert bleeding.feed("and my gum is bleeding too") == []


@pytest.mark.parametrize(
    "text",
    [
        "The pain is constant, no bleeding",
        "My toothache won't stop, it's not bleeding though",
        "Me duele la muela de continuo, no me sangra nada",
        "El dolor es constante, sin sangre",
    ],
)
def test_an_explicit_bleeding_denial_is_not_undone_by_constant_pain(text: str) -> None:
    assert redflags.match(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "it wouldn't stop bleeding",
        "the bleeding wouldn't stop",
        "it would not stop bleeding",
        "it was bleeding and it didn't stop",
        "the bleeding doesn't seem to stop",
        "I can't seem to stop the bleeding",
        "I don't think it's stopped bleeding",
        "le sangraba la encía y no paraba",
        "la sangre no paraba",
        "no se le paraba la sangre",
        "no se le cortaba la sangre",
        "seguía sangrando cuando llegamos a casa",
        "todavía no ha coagulado y le sangra",
        "it's been bleeding for 2 hours",
        "bleeding for the last hour",
        "the gum has been bleeding for about two hours",
        "it's been bleeding for 3 days",
        "it's been bleeding since the extraction",
        "lleva sangrando dos horas",
        "le sangra desde hace dos días",
        "sangra desde hace un par de horas",
        "hace dos horas que le sangra la boca",
        "no se me paró el sangrado",
        "no se le detenía el sangrado",
        "no se me frenaba la sangre",
        "we couldn't get the bleeding to stop",
        "it's been bleeding for like 45 minutes",
        "No sangraba al principio, pero ahora no para",
        "It wasn't bleeding this morning but now it won't stop",
        "At first there was no blood, but now it's pouring out and won't stop",
    ],
)
def test_round_three_bleeding_recall(text: str) -> None:
    assert "red_flag_bleeding" in redflags.match(text)


@pytest.mark.parametrize(
    "text",
    [
        "Se cayó en el parque de niños y se ha partido un diente",
        "Se ha caído en la zona de niños y se ha roto una paleta",
        "My son fell over when he was at school and his front tooth is loose",
        "No, me caí por las escaleras y me rompí un diente",
        "No, se cayó en el parque y tiene el diente roto",
        "No, hit in the face with a ball and my tooth is loose",
        "Vine hace poco, esta semana me he dado un golpe en la boca",
        "he fell off his bike same thing happened two years ago",
        "Me he dado un golpe en el diente que me empastaron hace un año",
    ],
)
def test_round_three_trauma_recall(text: str) -> None:
    assert "red_flag_trauma" in redflags.match(text)


def test_a_denial_does_not_cross_a_comma() -> None:
    assert redflags.match("No, fever of 39 and my cheek is swollen") == ["red_flag_swelling_fever"]
