"""Tests for the three per-call prompts."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import clinic, prompt
from seed.build import DEFAULT_SEED, build_db

ANCHOR = date(2026, 11, 16)
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=MADRID)

# contracts §2: the files in principle 4 never read the wall clock.
FORBIDDEN_CLOCK = ("datetime.now(", "datetime.utcnow(", "date.today(", "time.time(")

# Caller ID is phase 2 (decision D7): no prompt may hold it or hint at it.
CALLER_ID_PHRASES = (
    "caller id",
    "caller's number",
    "callerid",
    "identificador de llamada",
    "número desde el que llama",
    "numero desde el que llama",
    "número de teléfono del que llama",
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "clinic.db"
    assert build_db(db, ANCHOR, DEFAULT_SEED) == []
    return db


@pytest.fixture
def catalogue(db_path: Path) -> clinic.Catalogue:
    return clinic.load_catalogue(db_path)


def test_brain_prompt_sections_in_order(catalogue: clinic.Catalogue) -> None:
    text = prompt.brain_prompt(NOW, "es", catalogue)
    positions = [text.index(f"# {section}") for section in prompt.BRAIN_SECTIONS]
    assert positions == sorted(positions)


def test_brain_prompt_holds_the_calendar_and_the_catalogue(
    catalogue: clinic.Catalogue,
) -> None:
    text = prompt.brain_prompt(NOW, "es", catalogue)
    assert clinic.calendar_text(NOW, catalogue) in text
    assert clinic.catalogue_text(catalogue) in text
    assert "miércoles 18 de noviembre" in text
    assert catalogue.site.name in text


def test_brain_prompt_holds_the_rules_that_matter(catalogue: clinic.Catalogue) -> None:
    text = prompt.brain_prompt(NOW, "en", catalogue)
    # The closed question after a "no" (contracts §5.9, decision D19).
    assert "sí o no" in text
    assert "yes or no" in text
    assert "caller_said_no" in text
    assert "discarded: true" in text
    # Staging and confirmation.
    assert "confirm_pending" in text
    assert "discard_pending" in text
    assert "record_no_action" in text
    assert "find_patient" in text
    assert "search_availability" in text
    assert "record_triage" in text
    assert "take_message" in text
    # Emergencies and medical advice (contracts §6.2).
    assert "112" in text
    assert "emergency department" in text
    assert "never diagnose" in text
    assert "medical_advice_request" in text
    # Language, identification and the same-day rule.
    assert "stay in the new language until they switch" in text
    assert "full name and date of birth" in text
    assert "at least two hours from now" in text


def test_brain_prompt_has_no_caller_id_and_no_patient_phone(
    db_path: Path, catalogue: clinic.Catalogue
) -> None:
    text = prompt.brain_prompt(NOW, "es", catalogue)
    lowered = text.lower()
    for phrase in CALLER_ID_PHRASES:
        assert phrase not in lowered

    conn = sqlite3.connect(db_path)
    try:
        phones = [row[0] for row in conn.execute("SELECT phone FROM patients")]
    finally:
        conn.close()
    assert phones
    for phone in phones:
        assert phone not in text
        assert re.sub(r"\D", "", phone) not in text
    # No prompt reads out a number in digits: dates are the only digit runs.
    assert re.search(r"\d{7,}", text) is None


def test_brain_prompt_is_a_pure_function(catalogue: clinic.Catalogue) -> None:
    assert prompt.brain_prompt(NOW, "es", catalogue) == prompt.brain_prompt(NOW, "es", catalogue)
    assert prompt.brain_prompt(NOW, "es", catalogue) != prompt.brain_prompt(NOW, "en", catalogue)
    later = datetime(2026, 11, 18, 10, 0, tzinfo=MADRID)
    assert prompt.brain_prompt(later, "es", catalogue) != prompt.brain_prompt(NOW, "es", catalogue)


def test_brain_prompt_without_a_catalogue(catalogue: clinic.Catalogue) -> None:
    text = prompt.brain_prompt(NOW, "es", None)
    assert "catalogue is not available" in text
    assert "miércoles 18 de noviembre" in text  # the dates stay
    assert "2026-11-22" in text
    assert "closed, nobody works" not in text  # but no clinic status
    assert "Saturday staff only" not in text
    assert catalogue.site.name not in text
    assert "confirm_pending" in text


def test_brain_prompt_language_is_checked(catalogue: clinic.Catalogue) -> None:
    with pytest.raises(ValueError, match="unknown language"):
        prompt.brain_prompt(NOW, "fr", catalogue)


def test_cascade_prompt_only_appends_the_spoken_rules(catalogue: clinic.Catalogue) -> None:
    brain = prompt.brain_prompt(NOW, "es", catalogue)
    cascade = prompt.cascade_prompt(NOW, "es", catalogue)
    assert cascade.startswith(brain)
    assert cascade == brain + "\n\n" + prompt.SPOKEN_REPLY_RULES
    assert "no lists" in prompt.SPOKEN_REPLY_RULES
    assert "doctora" in prompt.SPOKEN_REPLY_RULES


def test_prompt_sizes(catalogue: clinic.Catalogue) -> None:
    brain = prompt.brain_prompt(NOW, "es", catalogue)
    assert 6_000 <= len(brain) <= 16_000
    assert len(prompt.voice_prompt("es", catalogue.site.name)) < 3_000


def test_voice_prompt_has_the_hand_off_and_no_clinic_facts(
    catalogue: clinic.Catalogue,
) -> None:
    text = prompt.voice_prompt("en", catalogue.site.name)
    assert "ask_clinic" in text
    assert catalogue.site.name in text
    assert "Do not greet the caller again" in text
    assert "medical advice" in text
    assert "be quiet" in text
    # No calendar, no prices, no catalogue facts.
    assert "2026" not in text
    assert "euros" not in text
    assert "EUR" not in text
    assert catalogue.site.address not in text
    assert "find_patient" not in text
    for practitioner in catalogue.practitioners:
        assert practitioner.name not in text
    lowered = text.lower()
    for phrase in CALLER_ID_PHRASES:
        assert phrase not in lowered


def test_voice_prompt_is_a_pure_function() -> None:
    assert prompt.voice_prompt("es", "A Clinic") == prompt.voice_prompt("es", "A Clinic")
    assert prompt.voice_prompt("es", "A Clinic") != prompt.voice_prompt("en", "A Clinic")
    with pytest.raises(ValueError, match="unknown language"):
        prompt.voice_prompt("de", "A Clinic")


@pytest.mark.parametrize("name", ("app/prompt.py", "app/clinic.py"))
def test_sources_read_no_wall_clock(name: str) -> None:
    source = (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")
    assert [token for token in FORBIDDEN_CLOCK if token in source] == []
