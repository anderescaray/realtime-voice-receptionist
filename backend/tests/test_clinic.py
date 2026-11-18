"""Tests for the clinic catalogue, its dated calendar and the spoken texts."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import clinic
from seed.build import DEFAULT_SEED, build_db

ANCHOR = date(2026, 11, 16)
MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def catalogue(tmp_path: Path) -> clinic.Catalogue:
    db = tmp_path / "clinic.db"
    assert build_db(db, ANCHOR, DEFAULT_SEED) == []
    return clinic.load_catalogue(db)


def day_lines(text: str) -> dict[str, str]:
    """The calendar's day lines, keyed on their ISO date."""
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    return {re.search(r"\d{4}-\d{2}-\d{2}", line).group(): line for line in lines}


def test_catalogue_loads_the_seeded_clinic(catalogue: clinic.Catalogue) -> None:
    assert len(catalogue.practitioners) == 5
    assert len(catalogue.treatments) == 6
    assert catalogue.site.name
    assert catalogue.site.address
    assert catalogue.site.timezone == "Europe/Madrid"
    assert catalogue.site.phone.startswith("+")
    hygienists = [p for p in catalogue.practitioners if p.role == "hygienist"]
    assert len(hygienists) == 1
    assert hygienists[0].treatments == ("cleaning",)
    assert catalogue.treatment("ortho_consult") is not None
    assert catalogue.treatment("ortho_consult").price_eur is None


def test_missing_database_raises(tmp_path: Path) -> None:
    with pytest.raises(clinic.CatalogueUnavailable):
        clinic.load_catalogue(tmp_path / "nothing.db")


def test_cache_keyed_on_the_file_not_the_date(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, ANCHOR, DEFAULT_SEED)
    first = clinic.load_catalogue(db)
    assert clinic.load_catalogue(db) is first

    build_db(db, date(2026, 11, 23), DEFAULT_SEED)
    second = clinic.load_catalogue(db)
    assert second is not first
    assert second.absences != first.absences
    assert clinic.load_catalogue(db) is second


def test_calendar_days_weekdays_and_labels(catalogue: clinic.Catalogue) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=MADRID)
    text = clinic.calendar_text(now, catalogue)
    lines = day_lines(text)
    assert len(lines) == 15
    assert list(lines) == [f"2026-11-{day:02d}" for day in range(17, 31)] + ["2026-12-01"]

    assert "Tue 17 Nov 2026 (martes 17 de noviembre)" in lines["2026-11-17"]
    assert "today / hoy" in lines["2026-11-17"]
    assert "Wed 18 Nov 2026 (miércoles 18 de noviembre)" in lines["2026-11-18"]
    assert "tomorrow / mañana" in lines["2026-11-18"]
    assert "Thu 19 Nov 2026 (jueves 19 de noviembre)" in lines["2026-11-19"]
    assert "the day after tomorrow / pasado mañana" in lines["2026-11-19"]
    assert "tomorrow" not in lines["2026-11-20"]
    assert "Europe/Madrid" in text
    assert "2026-12-01" in text.splitlines()[2]


def test_calendar_marks_sunday_saturday_and_absences(catalogue: clinic.Catalogue) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=MADRID)
    lines = day_lines(clinic.calendar_text(now, catalogue))

    assert "closed" in lines["2026-11-22"]
    assert "Sunday" in lines["2026-11-22"]

    saturday = lines["2026-11-21"]
    assert "Dr. Javier Moya Navarro" in saturday
    assert "09:00-14:00" in saturday
    assert "Dra. Carmen Iglesias Roldán" not in saturday

    # Absences are seeded relative to the anchor: hygienist on the 18th and 19th,
    # dent_4 on the 25th, dent_2 from the 30th.
    assert "Elena Torres Vidal (sick leave)" in lines["2026-11-18"]
    assert "Elena Torres Vidal (sick leave)" in lines["2026-11-19"]
    assert "away:" not in lines["2026-11-20"]
    assert "Dr. Andrés Peña Salazar (training)" in lines["2026-11-25"]
    assert "Dr. Javier Moya Navarro (annual leave)" in lines["2026-11-30"]
    assert "Dr. Javier Moya Navarro (annual leave)" in lines["2026-12-01"]


def test_calendar_names_december_holidays(catalogue: clinic.Catalogue) -> None:
    now = datetime(2026, 12, 1, 9, 0, tzinfo=MADRID)
    lines = day_lines(clinic.calendar_text(now, catalogue))
    assert "Día de la Constitución" in lines["2026-12-06"]
    assert "Inmaculada Concepción" in lines["2026-12-08"]
    assert "closed" in lines["2026-12-06"]
    assert "closed" in lines["2026-12-08"]


def test_calendar_crosses_a_month_boundary(catalogue: clinic.Catalogue) -> None:
    now = datetime(2026, 11, 25, 16, 30, tzinfo=MADRID)
    lines = day_lines(clinic.calendar_text(now, catalogue))
    assert list(lines)[0] == "2026-11-25"
    assert list(lines)[-1] == "2026-12-09"
    assert "Wed 25 Nov 2026" in lines["2026-11-25"]
    assert "Tue 1 Dec 2026 (martes 1 de diciembre)" in lines["2026-12-01"]


def test_calendar_uses_the_clinic_zone_not_utc(catalogue: clinic.Catalogue) -> None:
    """23:30 UTC on the 17th is already the 18th in Madrid."""
    now = datetime(2026, 11, 17, 23, 30, tzinfo=UTC)
    lines = day_lines(clinic.calendar_text(now, catalogue))
    assert list(lines)[0] == "2026-11-18"
    assert "today / hoy" in lines["2026-11-18"]
    assert "tomorrow / mañana" in lines["2026-11-19"]


def test_calendar_across_the_end_of_summer_time(catalogue: clinic.Catalogue) -> None:
    """Summer time ends on 25 October 2026: the dates must still run one by one."""
    now = datetime(2026, 10, 24, 12, 0, tzinfo=MADRID)
    lines = day_lines(clinic.calendar_text(now, catalogue))
    assert list(lines) == [f"2026-10-{d}" for d in range(24, 32)] + [
        f"2026-11-{d:02d}" for d in range(1, 8)
    ]
    assert "Sun 25 Oct 2026 (domingo 25 de octubre)" in lines["2026-10-25"]
    assert "Todos los Santos" in lines["2026-11-01"]


def test_calendar_without_a_catalogue_keeps_its_dates() -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=MADRID)
    lines = day_lines(clinic.calendar_text(now, None))
    assert len(lines) == 15
    assert lines["2026-11-22"].endswith("2026-11-22")
    assert "closed" not in lines["2026-11-22"]


def test_calendar_window_length(catalogue: clinic.Catalogue) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=MADRID)
    assert len(day_lines(clinic.calendar_text(now, catalogue, days=6))) == 7


def test_spoken_phone_uk_number(catalogue: clinic.Catalogue) -> None:
    assert clinic.spoken_phone("+442079460123", "en") == (
        "plus four four, two zero seven, nine four six, zero one two three"
    )
    assert clinic.spoken_phone("+442079460123", "es") == (
        "más cuatro cuatro, dos cero siete, nueve cuatro seis, cero uno dos tres"
    )
    # The seeded clinic number, as the clips and the agent say it.
    assert clinic.spoken_phone(catalogue.site.phone, "en") == (
        "plus four four, two zero seven, nine four six, zero zero zero zero"
    )


def test_spoken_phone_spanish_number_drops_the_country_code() -> None:
    assert clinic.spoken_phone("+34910123456", "es") == (
        "nueve uno cero, uno dos tres, cuatro cinco seis"
    )
    assert clinic.spoken_phone("+34910123456", "en") == (
        "nine one zero, one two three, four five six"
    )


def test_spoken_phone_unknown_language() -> None:
    with pytest.raises(ValueError, match="unknown language"):
        clinic.spoken_phone("+34910123456", "fr")


@pytest.mark.parametrize("topic", clinic.TOPICS)
@pytest.mark.parametrize("lang", clinic.LANGUAGES)
def test_topic_text_is_short_and_complete(
    topic: str, lang: str, catalogue: clinic.Catalogue
) -> None:
    text = clinic.topic_text(topic, lang, catalogue)
    assert text.strip()
    assert "None" not in text
    assert len(text) <= 600


def test_topic_text_prices_and_hours(catalogue: clinic.Catalogue) -> None:
    prices_en = clinic.topic_text("prices", "en", catalogue)
    assert "price given at the clinic" in prices_en
    assert "40 euros" in prices_en
    prices_es = clinic.topic_text("prices", "es", catalogue)
    assert "el precio se da en la clínica" in prices_es

    hours = clinic.topic_text("hours", "en", catalogue)
    assert "Monday to Friday 08:00-20:00" in hours
    assert "Saturday 09:00-14:00" in hours
    assert "Sunday closed" in hours

    contact = clinic.topic_text("contact", "es", catalogue)
    assert clinic.spoken_phone(catalogue.site.phone, "es") in contact
    assert catalogue.site.address in contact

    location = clinic.topic_text("location", "en", catalogue)
    assert catalogue.site.address in location
    assert "maps app" in location


def test_topic_text_refuses_unknown_topic_and_language(catalogue: clinic.Catalogue) -> None:
    with pytest.raises(ValueError, match="unknown topic"):
        clinic.topic_text("parking", "es", catalogue)
    with pytest.raises(ValueError, match="unknown language"):
        clinic.topic_text("hours", "de", catalogue)


def test_catalogue_text_holds_the_clinic_facts(catalogue: clinic.Catalogue) -> None:
    text = clinic.catalogue_text(catalogue)
    assert catalogue.site.name in text
    assert catalogue.site.address in text
    assert clinic.spoken_phone(catalogue.site.phone, "es") in text
    assert clinic.spoken_phone(catalogue.site.phone, "en") in text
    assert "Mon-Fri 08:00-20:00" in text
    for practitioner in catalogue.practitioners:
        assert practitioner.name in text
        assert practitioner.practitioner_id in text
    for treatment in catalogue.treatments:
        assert treatment.name_es in text
        assert treatment.name_en in text
    assert "price given at the clinic" in text
    assert "None" not in text
    assert clinic.catalogue_text(catalogue, "es") != text


def test_opening_hours_are_the_union_of_the_practitioners(catalogue: clinic.Catalogue) -> None:
    hours = clinic.opening_hours(catalogue)
    assert hours[0] == (("08:00", "20:00"),)  # Monday, one continuous span
    assert hours[5] == (("09:00", "14:00"),)  # Saturday
    assert 6 not in hours  # nobody works on Sunday
