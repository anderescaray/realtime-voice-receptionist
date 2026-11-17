"""Tests for the slot engine: every rule on a fixed clock, never the wall clock."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.slots import (
    DEFAULT_BOOKING_HORIZON_DAYS,
    GRID_MINUTES,
    SlotSearch,
    SlotSearchError,
    check_slot,
    search_slots,
)
from seed.build import DEFAULT_SEED, build_db

MADRID = ZoneInfo("Europe/Madrid")
BACKEND = Path(__file__).resolve().parent.parent
SCHEMA = (BACKEND / "engine" / "schema.sql").read_text(encoding="utf-8")

WEEKDAYS = (0, 1, 2, 3, 4)
MINUTES = {"checkup": 30, "cleaning": 45, "extraction": 60, "urgency": 30}

MONDAY = date(2026, 11, 16)
TUESDAY = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MADRID)

FORBIDDEN_CLOCK = ("datetime.now(", "datetime.utcnow(", "date.today(", "time.time(")


def local(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MADRID)


def utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


class Clinic:
    """A hand-made clinic database holding only the rows one test needs."""

    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        for treatment, minutes in MINUTES.items():
            self.conn.execute(
                "INSERT INTO treatments (treatment_id, name_es, name_en, minutes, price_eur)"
                " VALUES (?, ?, ?, ?, NULL)",
                (treatment, treatment, treatment, minutes),
            )
        self._patients: set[str] = set()
        self._appointments = 0

    def practitioner(
        self,
        practitioner_id: str,
        *,
        treatments: tuple[str, ...] = ("checkup",),
        hours: tuple[tuple[str, str], ...] = (("09:00", "14:00"),),
        weekdays: tuple[int, ...] = WEEKDAYS,
        role: str = "dentist",
    ) -> Clinic:
        self.conn.execute(
            "INSERT INTO practitioners (practitioner_id, name, role, languages)"
            " VALUES (?, ?, ?, 'es')",
            (practitioner_id, practitioner_id, role),
        )
        for treatment in treatments:
            self.conn.execute(
                "INSERT INTO practitioner_treatments (practitioner_id, treatment_id) VALUES (?, ?)",
                (practitioner_id, treatment),
            )
        for weekday in weekdays:
            for start, end in hours:
                self.conn.execute(
                    "INSERT INTO working_hours (practitioner_id, weekday, start_local, end_local)"
                    " VALUES (?, ?, ?, ?)",
                    (practitioner_id, weekday, start, end),
                )
        return self

    def closure(self, day: date, name: str = "día festivo") -> Clinic:
        self.conn.execute(
            "INSERT INTO closures (date, name) VALUES (?, ?)", (day.isoformat(), name)
        )
        return self

    def absence(
        self,
        practitioner_id: str,
        start_date: date,
        end_date: date | None = None,
        *,
        start_local: str | None = None,
        end_local: str | None = None,
        reason: str = "annual_leave",
    ) -> Clinic:
        self.conn.execute(
            "INSERT INTO absences (practitioner_id, start_date, end_date, reason,"
            " start_local, end_local) VALUES (?, ?, ?, ?, ?, ?)",
            (
                practitioner_id,
                start_date.isoformat(),
                (end_date or start_date).isoformat(),
                reason,
                start_local,
                end_local,
            ),
        )
        return self

    def patient(self, patient_id: str) -> Clinic:
        if patient_id not in self._patients:
            self.conn.execute(
                "INSERT INTO patients (patient_id, first_name, last_name1, last_name2, dob,"
                " phone, language, created_by_call)"
                " VALUES (?, 'Nombre', 'Apellido', NULL, '1990-01-01', '+34700000000', 'es', NULL)",
                (patient_id,),
            )
            self._patients.add(patient_id)
        return self

    def appointment(
        self,
        practitioner_id: str,
        day: date,
        start_hhmm: str,
        *,
        treatment: str = "checkup",
        patient_id: str = "pat_1",
        status: str = "booked",
        appointment_id: str | None = None,
    ) -> str:
        self.patient(patient_id)
        self._appointments += 1
        aid = appointment_id or f"a_{self._appointments:04d}"
        start = local(day, start_hhmm)
        end = start + timedelta(minutes=MINUTES[treatment])
        self.conn.execute(
            "INSERT INTO appointments (appointment_id, patient_id, practitioner_id,"
            " treatment_id, start_utc, end_utc, status, same_day_urgent, created_by_call)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)",
            (aid, patient_id, practitioner_id, treatment, utc_text(start), utc_text(end), status),
        )
        return aid


@pytest.fixture
def clinic(tmp_path: Path) -> Clinic:
    return Clinic(tmp_path / "clinic.db")


@pytest.fixture(scope="module")
def november_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("seed-november") / "clinic.db"
    assert build_db(path, date(2026, 11, 16), DEFAULT_SEED) == []
    return path


@pytest.fixture(scope="module")
def spring_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An anchor whose 28-day window crosses the spring clock change of 2027-03-28."""
    path = tmp_path_factory.mktemp("seed-spring") / "clinic.db"
    assert build_db(path, date(2027, 3, 22), DEFAULT_SEED) == []
    return path


@pytest.fixture
def november(november_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(november_db)
    yield conn
    conn.close()


@pytest.fixture
def spring(spring_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(spring_db)
    yield conn
    conn.close()


def reason_codes(result: SlotSearch, practitioner_id: str) -> list[str]:
    entry = next(b for b in result.blocked if b.practitioner_id == practitioner_id)
    return [r.reason for r in entry.reasons]


def reason_for(result: SlotSearch, practitioner_id: str, code: str):
    entry = next(b for b in result.blocked if b.practitioner_id == practitioner_id)
    return next(r for r in entry.reasons if r.reason == code)


def starts_on(result: SlotSearch, day: date, practitioner_id: str | None = None) -> list[str]:
    return [
        hhmm(s.start)
        for s in result.slots
        if s.start.date() == day and practitioner_id in (None, s.practitioner_id)
    ]


# --- the grid --------------------------------------------------------------


def test_every_slot_starts_on_a_quarter_hour(november: sqlite3.Connection) -> None:
    result = search_slots(november, now=NOW, treatment="cleaning", limit=50)
    assert result.slots
    for slot in result.slots:
        assert slot.start.minute % GRID_MINUTES == 0
        assert slot.start.second == 0


def test_last_start_leaves_room_before_the_end_of_hours(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("extraction",), hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn, now=NOW, treatment="extraction", after=TUESDAY, before=TUESDAY, limit=50
    )
    starts = starts_on(result, TUESDAY)
    assert starts[0] == "09:00"
    assert starts[-1] == "13:00"
    assert "13:15" not in starts


def test_split_shift_never_runs_across_the_break(clinic: Clinic) -> None:
    clinic.practitioner(
        "d1", treatments=("extraction",), hours=(("09:00", "14:00"), ("16:00", "20:00"))
    )
    result = search_slots(
        clinic.conn, now=NOW, treatment="extraction", after=TUESDAY, before=TUESDAY, limit=50
    )
    starts = starts_on(result, TUESDAY)
    assert "13:30" not in starts
    assert "15:45" not in starts
    assert "16:00" in starts
    assert starts[-1] == "19:00"


def test_slots_lie_inside_a_working_interval(november: sqlite3.Connection) -> None:
    result = search_slots(november, now=NOW, treatment="extraction", limit=50)
    assert result.slots
    hours = november.execute(
        "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
    ).fetchall()
    for slot in result.slots:
        intervals = [
            (start, end)
            for pid, weekday, start, end in hours
            if pid == slot.practitioner_id and weekday == slot.start.weekday()
        ]
        assert any(start <= hhmm(slot.start) and hhmm(slot.end) <= end for start, end in intervals)


# --- one blocking reason at a time -----------------------------------------


def test_clinic_closed(clinic: Clinic) -> None:
    clinic.practitioner("d1").closure(TUESDAY, "Fiesta local")
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY)
    assert result.slots == []
    assert reason_codes(result, "d1") == ["clinic_closed"]
    assert reason_for(result, "d1", "clinic_closed").dates == [TUESDAY.isoformat()]


def test_absent_whole_day_carries_until(clinic: Clinic) -> None:
    clinic.practitioner("d1").absence("d1", TUESDAY, date(2026, 11, 20))
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=WEDNESDAY
    )
    assert result.slots == []
    absent = reason_for(result, "d1", "absent")
    assert absent.dates == [TUESDAY.isoformat(), WEDNESDAY.isoformat()]
    assert absent.until == "2026-11-20"


def test_part_day_absence_leaves_the_rest_of_the_day(clinic: Clinic) -> None:
    clinic.practitioner("d1").absence("d1", TUESDAY, start_local="09:00", end_local="11:00")
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY, limit=50
    )
    starts = starts_on(result, TUESDAY)
    assert starts[0] == "11:00"
    assert "10:30" not in starts
    assert result.blocked == []


def test_part_day_absence_over_the_whole_shift_reports_absent(clinic: Clinic) -> None:
    clinic.practitioner("d1").absence(
        "d1", TUESDAY, date(2026, 11, 19), start_local="08:00", end_local="20:00"
    )
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY)
    assert result.slots == []
    assert reason_for(result, "d1", "absent").until == "2026-11-19"


def test_not_working_that_day(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=(0,))
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY)
    assert result.slots == []
    assert reason_codes(result, "d1") == ["not_working_that_day"]


def test_fully_booked(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.appointment("d1", TUESDAY, "09:00")
    clinic.appointment("d1", TUESDAY, "09:30", patient_id="pat_2")
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY)
    assert result.slots == []
    assert reason_codes(result, "d1") == ["fully_booked"]


def test_outside_part_of_day(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=TUESDAY,
        before=TUESDAY,
        part_of_day="afternoon",
    )
    assert result.slots == []
    assert reason_codes(result, "d1") == ["outside_part_of_day"]


def test_afternoon_starts_at_two(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("13:00", "16:00"),))
    morning = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=TUESDAY,
        before=TUESDAY,
        part_of_day="morning",
        limit=50,
    )
    afternoon = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=TUESDAY,
        before=TUESDAY,
        part_of_day="afternoon",
        limit=50,
    )
    assert starts_on(morning, TUESDAY)[-1] == "13:45"
    assert starts_on(afternoon, TUESDAY)[0] == "14:00"


def test_patient_busy(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.practitioner("d2", hours=(("09:00", "10:00"),))
    clinic.appointment("d2", TUESDAY, "09:00", patient_id="pat_1")
    clinic.appointment("d2", TUESDAY, "09:30", patient_id="pat_1", appointment_id="a_second")
    result = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        practitioner_id="d1",
        after=TUESDAY,
        before=TUESDAY,
        patient_id="pat_1",
    )
    assert result.slots == []
    assert reason_codes(result, "d1") == ["patient_busy"]


def test_beyond_horizon(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=())
    far = MONDAY + timedelta(days=40)
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", before=far)
    assert result.window_end == MONDAY + timedelta(days=DEFAULT_BOOKING_HORIZON_DAYS)
    codes = reason_codes(result, "d1")
    assert codes[-1] == "beyond_horizon"
    assert reason_for(result, "d1", "beyond_horizon").dates == []


def test_after_past_the_horizon_without_before_is_beyond_horizon(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    far = MONDAY + timedelta(days=40)
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=far)
    assert result.slots == []
    assert reason_codes(result, "d1") == ["beyond_horizon"]


def test_within_lead_time(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "11:00"),))
    result = search_slots(
        clinic.conn, now=NOW, treatment="urgency", after=MONDAY, before=MONDAY, limit=50
    )
    assert result.slots == []
    assert reason_codes(result, "d1") == ["within_lead_time"]


def test_reasons_group_by_code_with_their_dates(clinic: Clinic) -> None:
    clinic.practitioner(
        "d1", treatments=("cleaning",), hours=(("09:00", "10:30"),), weekdays=(1, 2, 3)
    )
    clinic.closure(WEDNESDAY)
    clinic.absence("d1", date(2026, 11, 19), date(2026, 11, 21))
    clinic.appointment("d1", TUESDAY, "09:00", treatment="cleaning")
    clinic.appointment("d1", TUESDAY, "09:45", treatment="cleaning", patient_id="pat_2")
    result = search_slots(
        clinic.conn, now=NOW, treatment="cleaning", after=TUESDAY, before=date(2026, 11, 22)
    )
    assert result.slots == []
    assert reason_codes(result, "d1") == [
        "fully_booked",
        "clinic_closed",
        "absent",
        "not_working_that_day",
    ]
    assert reason_for(result, "d1", "fully_booked").dates == ["2026-11-17"]
    assert reason_for(result, "d1", "clinic_closed").dates == ["2026-11-18"]
    assert reason_for(result, "d1", "absent").dates == ["2026-11-19", "2026-11-20", "2026-11-21"]
    assert reason_for(result, "d1", "not_working_that_day").dates == ["2026-11-22"]


def test_a_blocked_entry_always_has_a_reason(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=())
    result = search_slots(clinic.conn, now=NOW, treatment="checkup")
    assert result.slots == []
    assert [b.practitioner_id for b in result.blocked] == ["d1"]
    assert all(b.reasons for b in result.blocked)


# --- appointments ----------------------------------------------------------


def test_cancelled_appointments_do_not_block(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.appointment("d1", TUESDAY, "09:00", status="cancelled")
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY, limit=50
    )
    assert starts_on(result, TUESDAY) == ["09:00", "09:15", "09:30"]


def test_touching_appointments_do_not_overlap(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.appointment("d1", TUESDAY, "09:00")
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY, limit=50
    )
    assert starts_on(result, TUESDAY) == ["09:30"]


# --- the same day ----------------------------------------------------------


def test_today_is_never_offered_outside_urgency(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "20:00"),))
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", limit=50)
    assert result.window_start == TUESDAY
    assert all(not slot.same_day for slot in result.slots)
    assert all(slot.start.date() > MONDAY for slot in result.slots)


def test_urgency_offers_today_after_the_lead_time(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn, now=NOW, treatment="urgency", after=MONDAY, before=MONDAY, limit=50
    )
    starts = starts_on(result, MONDAY)
    assert starts[0] == "12:00"
    assert "11:45" not in starts
    assert all(slot.same_day for slot in result.slots)


def test_urgency_lead_time_can_be_shortened(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn,
        now=NOW,
        treatment="urgency",
        after=MONDAY,
        before=MONDAY,
        limit=50,
        urgency_lead_minutes=30,
    )
    assert starts_on(result, MONDAY)[0] == "10:30"


def test_urgency_after_closing_time_is_within_lead_time(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "20:00"),))
    late = datetime(2026, 11, 16, 21, 0, tzinfo=MADRID)
    result = search_slots(clinic.conn, now=late, treatment="urgency", after=MONDAY, before=MONDAY)
    assert result.slots == []
    assert reason_codes(result, "d1") == ["within_lead_time"]


# --- the window ------------------------------------------------------------


def test_horizon_end_is_inclusive(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=tuple(range(7)))
    last = MONDAY + timedelta(days=DEFAULT_BOOKING_HORIZON_DAYS)
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=last, before=last, limit=1
    )
    assert [s.start.date() for s in result.slots] == [last]

    day_after = last + timedelta(days=1)
    past_last = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=day_after, before=day_after
    )
    assert past_last.slots == []
    assert past_last.window_end == last
    assert reason_codes(past_last, "d1") == ["beyond_horizon"]


def test_after_and_before_are_inclusive(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=tuple(range(7)))
    result = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=WEDNESDAY,
        before=date(2026, 11, 19),
        limit=200,
    )
    days = sorted({slot.start.date() for slot in result.slots})
    assert days == [WEDNESDAY, date(2026, 11, 19)]
    assert result.window_start == WEDNESDAY
    assert result.window_end == date(2026, 11, 19)


def test_after_is_clipped_to_the_window_start(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=tuple(range(7)))
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=date(2026, 1, 1), limit=1
    )
    assert result.window_start == TUESDAY
    assert result.slots[0].start.date() == TUESDAY


@pytest.mark.parametrize(
    ("after", "before", "part_of_day"),
    (
        (date(2026, 11, 20), date(2026, 11, 19), None),
        (None, MONDAY, None),
        (None, date(2026, 11, 15), None),
        (None, None, "evening"),
    ),
)
def test_bad_range(
    clinic: Clinic, after: date | None, before: date | None, part_of_day: str | None
) -> None:
    clinic.practitioner("d1")
    with pytest.raises(SlotSearchError) as caught:
        search_slots(
            clinic.conn,
            now=NOW,
            treatment="checkup",
            after=after,
            before=before,
            part_of_day=part_of_day,
        )
    assert caught.value.code == "bad_range"


def test_urgency_may_search_today(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "20:00"),))
    result = search_slots(clinic.conn, now=NOW, treatment="urgency", before=MONDAY, limit=1)
    assert result.window_start == MONDAY
    assert result.slots[0].same_day is True


def test_unknown_treatment(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    with pytest.raises(SlotSearchError) as caught:
        search_slots(clinic.conn, now=NOW, treatment="whitening")
    assert caught.value.code == "unknown_treatment"


def test_unknown_practitioner(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    with pytest.raises(SlotSearchError) as caught:
        search_slots(clinic.conn, now=NOW, treatment="checkup", practitioner_id="d9")
    assert caught.value.code == "unknown_practitioner"


def test_treatment_mismatch(november: sqlite3.Connection) -> None:
    with pytest.raises(SlotSearchError) as caught:
        search_slots(november, now=NOW, treatment="extraction", practitioner_id="hyg_1")
    assert caught.value.code == "treatment_mismatch"


def test_naive_now_is_a_programming_error(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    with pytest.raises(ValueError, match="naive"):
        search_slots(clinic.conn, now=datetime(2026, 11, 16, 10, 0), treatment="checkup")  # noqa: DTZ001


# --- moving an appointment -------------------------------------------------


def test_excluded_appointment_frees_both_diaries(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.practitioner("d2", hours=(("09:00", "10:00"),))
    aid = clinic.appointment("d1", TUESDAY, "09:00", patient_id="pat_1")
    clinic.appointment("d1", TUESDAY, "09:30", patient_id="pat_2")

    held = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=TUESDAY,
        before=TUESDAY,
        patient_id="pat_1",
        limit=50,
    )
    assert starts_on(held, TUESDAY, "d1") == []
    assert starts_on(held, TUESDAY, "d2") == ["09:30"]

    freed = search_slots(
        clinic.conn,
        now=NOW,
        treatment="checkup",
        after=TUESDAY,
        before=TUESDAY,
        patient_id="pat_1",
        exclude_appointment_id=aid,
        limit=50,
    )
    assert starts_on(freed, TUESDAY, "d1") == ["09:00"]
    assert starts_on(freed, TUESDAY, "d2") == ["09:00", "09:15", "09:30"]


# --- order, limit and determinism ------------------------------------------


def test_order_is_time_then_practitioner(clinic: Clinic) -> None:
    clinic.practitioner("d2", hours=(("09:00", "14:00"),))
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY, limit=4
    )
    assert [(hhmm(s.start), s.practitioner_id) for s in result.slots] == [
        ("09:00", "d1"),
        ("09:00", "d2"),
        ("09:15", "d1"),
        ("09:15", "d2"),
    ]


def test_slots_past_the_limit_keep_a_practitioner_out_of_blocked(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    clinic.practitioner("d2", hours=(("09:00", "14:00"),))
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY, limit=1
    )
    assert len(result.slots) == 1
    assert result.blocked == []


def test_two_identical_calls_agree(november: sqlite3.Connection) -> None:
    first = search_slots(november, now=NOW, treatment="cleaning", limit=5)
    second = search_slots(november, now=NOW, treatment="cleaning", limit=5)
    assert first == second
    assert first.as_dict() == second.as_dict()


def test_the_same_moment_in_utc_or_madrid_gives_the_same_answer(
    november: sqlite3.Connection,
) -> None:
    in_madrid = search_slots(november, now=NOW, treatment="urgency", limit=5)
    in_utc = search_slots(november, now=NOW.astimezone(UTC), treatment="urgency", limit=5)
    assert in_madrid == in_utc


# --- check_slot ------------------------------------------------------------


def test_check_slot_accepts_every_slot_of_a_search(november: sqlite3.Connection) -> None:
    for treatment in ("checkup", "cleaning", "extraction", "ortho_consult", "urgency"):
        result = search_slots(november, now=NOW, treatment=treatment, limit=25)
        assert result.slots
        for slot in result.slots:
            assert (
                check_slot(
                    november,
                    now=NOW,
                    practitioner_id=slot.practitioner_id,
                    treatment=slot.treatment,
                    start=slot.start,
                )
                is None
            )


def test_check_slot_treatment_mismatch(november: sqlite3.Connection) -> None:
    assert (
        check_slot(
            november,
            now=NOW,
            practitioner_id="hyg_1",
            treatment="extraction",
            start=local(TUESDAY, "10:00"),
        )
        == "treatment_mismatch"
    )


def test_check_slot_same_day(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "20:00"),))
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="checkup",
            start=local(MONDAY, "18:00"),
        )
        == "same_day"
    )


def test_check_slot_too_soon(clinic: Clinic) -> None:
    clinic.practitioner("d1", treatments=("urgency",), hours=(("09:00", "20:00"),))
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="urgency",
            start=local(MONDAY, "11:00"),
        )
        == "too_soon"
    )
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="urgency",
            start=local(MONDAY, "09:00"),
        )
        == "too_soon"
    )
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="urgency",
            start=local(MONDAY, "12:00"),
        )
        is None
    )


@pytest.mark.parametrize(
    ("day", "start_hhmm"),
    (
        (TUESDAY, "13:30"),  # a 60-minute treatment would run across the break
        (TUESDAY, "10:07"),  # off the 15-minute grid
        (TUESDAY, "08:00"),  # before the first interval
        (date(2026, 11, 14), "10:00"),  # a Saturday, not worked
        (date(2026, 11, 15), "10:00"),  # in the past
        (MONDAY + timedelta(days=29), "10:00"),  # beyond the horizon
    ),
)
def test_check_slot_slot_taken_cases(clinic: Clinic, day: date, start_hhmm: str) -> None:
    clinic.practitioner(
        "d1", treatments=("extraction",), hours=(("09:00", "14:00"), ("16:00", "20:00"))
    )
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="extraction",
            start=local(day, start_hhmm),
        )
        == "slot_taken"
    )


def test_check_slot_slot_taken_on_closure_and_absence(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    clinic.closure(TUESDAY)
    clinic.absence("d1", WEDNESDAY)
    for day in (TUESDAY, WEDNESDAY):
        assert (
            check_slot(
                clinic.conn,
                now=NOW,
                practitioner_id="d1",
                treatment="checkup",
                start=local(day, "10:00"),
            )
            == "slot_taken"
        )


def test_check_slot_loses_the_race(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    start = local(TUESDAY, "10:00")
    assert (
        check_slot(clinic.conn, now=NOW, practitioner_id="d1", treatment="checkup", start=start)
        is None
    )
    clinic.appointment("d1", TUESDAY, "10:00", patient_id="pat_other")
    assert (
        check_slot(clinic.conn, now=NOW, practitioner_id="d1", treatment="checkup", start=start)
        == "slot_taken"
    )


def test_check_slot_patient_overlap(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    clinic.practitioner("d2", hours=(("09:00", "14:00"),))
    aid = clinic.appointment("d2", TUESDAY, "10:00", patient_id="pat_1")
    start = local(TUESDAY, "10:15")
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="checkup",
            start=start,
            patient_id="pat_1",
        )
        == "patient_overlap"
    )
    assert (
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="checkup",
            start=start,
            patient_id="pat_1",
            exclude_appointment_id=aid,
        )
        is None
    )


def test_check_slot_refuses_a_treatment_the_clinic_does_not_offer(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    with pytest.raises(SlotSearchError) as caught:
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="whitening",
            start=local(TUESDAY, "10:00"),
        )
    assert caught.value.code == "unknown_treatment"


def test_check_slot_rejects_a_naive_start(clinic: Clinic) -> None:
    clinic.practitioner("d1")
    with pytest.raises(ValueError, match="naive"):
        check_slot(
            clinic.conn,
            now=NOW,
            practitioner_id="d1",
            treatment="checkup",
            start=datetime(2026, 11, 17, 10, 0),  # noqa: DTZ001
        )


# --- clock changes ---------------------------------------------------------


def test_spring_change_switches_the_offset(spring: sqlite3.Connection) -> None:
    before = search_slots(
        spring,
        now=datetime(2027, 3, 22, 10, 0, tzinfo=MADRID),
        treatment="checkup",
        after=date(2027, 3, 26),
        before=date(2027, 3, 26),
        limit=50,
    )
    after = search_slots(
        spring,
        now=datetime(2027, 3, 22, 10, 0, tzinfo=MADRID),
        treatment="checkup",
        after=date(2027, 3, 29),
        before=date(2027, 3, 29),
        limit=50,
    )
    assert before.slots and after.slots
    assert {s.start.isoformat()[-6:] for s in before.slots} == {"+01:00"}
    assert {s.start.isoformat()[-6:] for s in after.slots} == {"+02:00"}
    assert {s.end.isoformat()[-6:] for s in after.slots} == {"+02:00"}


def test_summer_appointment_blocks_the_local_hour(tmp_path: Path) -> None:
    day = date(2027, 3, 29)  # the Monday after the spring change: Madrid is UTC+2
    clinic = Clinic(tmp_path / "summer.db")
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    clinic.appointment("d1", day, "10:00", patient_id="pat_1")
    stored = clinic.conn.execute("SELECT start_utc FROM appointments").fetchone()[0]
    assert stored == "2027-03-29T08:00:00Z"

    result = search_slots(
        clinic.conn,
        now=datetime(2027, 3, 22, 10, 0, tzinfo=MADRID),
        treatment="checkup",
        after=day,
        before=day,
        limit=50,
    )
    starts = starts_on(result, day)
    assert "10:00" not in starts
    assert "11:00" in starts


def test_autumn_change_switches_the_offset(tmp_path: Path) -> None:
    clinic = Clinic(tmp_path / "autumn.db")
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    now = datetime(2026, 10, 22, 10, 0, tzinfo=MADRID)  # the clocks go back on 2026-10-25
    summer = search_slots(
        clinic.conn,
        now=now,
        treatment="checkup",
        after=date(2026, 10, 23),
        before=date(2026, 10, 23),
        limit=50,
    )
    winter = search_slots(
        clinic.conn,
        now=now,
        treatment="checkup",
        after=date(2026, 10, 26),
        before=date(2026, 10, 26),
        limit=50,
    )
    assert {s.start.isoformat()[-6:] for s in summer.slots} == {"+02:00"}
    assert {s.start.isoformat()[-6:] for s in winter.slots} == {"+01:00"}


def test_winter_appointment_blocks_the_local_hour(tmp_path: Path) -> None:
    day = date(2026, 10, 26)  # the Monday after the autumn change: Madrid is UTC+1
    clinic = Clinic(tmp_path / "winter.db")
    clinic.practitioner("d1", hours=(("09:00", "14:00"),))
    clinic.appointment("d1", day, "10:00", patient_id="pat_1")
    assert clinic.conn.execute("SELECT start_utc FROM appointments").fetchone()[0] == (
        "2026-10-26T09:00:00Z"
    )
    result = search_slots(
        clinic.conn,
        now=datetime(2026, 10, 22, 10, 0, tzinfo=MADRID),
        treatment="checkup",
        after=day,
        before=day,
        limit=50,
    )
    starts = starts_on(result, day)
    assert "10:00" not in starts
    assert "11:00" in starts


# --- the seeded clinic -----------------------------------------------------


def test_seeded_fully_booked_fixture(november: sqlite3.Connection) -> None:
    fixtures = json.loads(
        november.execute("SELECT value FROM meta WHERE key = 'fixtures'").fetchone()[0]
    )
    fixture = fixtures["fully_booked"]
    day = date.fromisoformat(fixture["date"])
    result = search_slots(
        november,
        now=NOW,
        treatment="checkup",
        practitioner_id=fixture["practitioner_id"],
        after=day,
        before=day,
    )
    assert result.slots == []
    reason = reason_for(result, fixture["practitioner_id"], "fully_booked")
    assert reason.dates == [fixture["date"]]


def test_seeded_absence_reports_until(november: sqlite3.Connection) -> None:
    practitioner_id, start_date, end_date = november.execute(
        "SELECT practitioner_id, start_date, end_date FROM absences ORDER BY start_date LIMIT 1"
    ).fetchone()
    day = date.fromisoformat(start_date)
    treatment = november.execute(
        "SELECT treatment_id FROM practitioner_treatments WHERE practitioner_id = ?"
        " ORDER BY treatment_id LIMIT 1",
        (practitioner_id,),
    ).fetchone()[0]
    result = search_slots(
        november,
        now=NOW,
        treatment=treatment,
        practitioner_id=practitioner_id,
        after=day,
        before=day,
    )
    assert result.slots == []
    assert reason_for(result, practitioner_id, "absent").until == end_date


def test_seeded_holiday_closes_the_clinic(november: sqlite3.Connection) -> None:
    holiday = date(2026, 12, 8)
    result = search_slots(november, now=NOW, treatment="checkup", after=holiday, before=holiday)
    assert result.slots == []
    assert result.blocked
    for entry in result.blocked:
        assert [r.reason for r in entry.reasons] == ["clinic_closed"]
        assert entry.reasons[0].dates == [holiday.isoformat()]


def test_a_full_window_search_is_quick(november: sqlite3.Connection) -> None:
    started = time.perf_counter()
    for treatment in ("checkup", "cleaning", "filling", "extraction", "ortho_consult", "urgency"):
        search_slots(november, now=NOW, treatment=treatment)
    assert time.perf_counter() - started < 1.0


def test_searching_one_practitioner_ignores_the_others(november: sqlite3.Connection) -> None:
    result = search_slots(november, now=NOW, treatment="checkup", practitioner_id="dent_3")
    assert {slot.practitioner_id for slot in result.slots} == {"dent_3"}
    assert all(entry.practitioner_id == "dent_3" for entry in result.blocked)


def test_seed_still_builds_against_the_new_absence_columns(tmp_path: Path) -> None:
    path = tmp_path / "clinic.db"
    assert build_db(path, date(2026, 11, 16), DEFAULT_SEED) == []
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT start_local, end_local FROM absences").fetchall()
    finally:
        conn.close()
    assert rows and all(row == (None, None) for row in rows)


def test_part_day_absence_columns_must_come_in_pairs(tmp_path: Path) -> None:
    clinic = Clinic(tmp_path / "clinic.db")
    clinic.practitioner("d1")
    with pytest.raises(sqlite3.IntegrityError):
        clinic.absence("d1", TUESDAY, start_local="09:00")
    with pytest.raises(sqlite3.IntegrityError):
        clinic.absence("d1", TUESDAY, start_local="12:00", end_local="09:00")


# --- shapes and hygiene ----------------------------------------------------


def test_as_dict_is_json_serialisable(clinic: Clinic) -> None:
    clinic.practitioner("d1", hours=(("09:00", "10:00"),))
    clinic.practitioner("d2", weekdays=(0,))
    clinic.absence("d2", TUESDAY, date(2026, 11, 19))
    result = search_slots(clinic.conn, now=NOW, treatment="checkup", after=TUESDAY, before=TUESDAY)
    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["window_start"] == "2026-11-17"
    assert payload["window_end"] == "2026-11-17"
    assert payload["slots"][0] == {
        "practitioner_id": "d1",
        "treatment": "checkup",
        "start": "2026-11-17T09:00:00+01:00",
        "end": "2026-11-17T09:30:00+01:00",
        "same_day": False,
    }
    assert payload["blocked"] == [
        {
            "practitioner_id": "d2",
            "reasons": [
                {"reason": "absent", "dates": ["2026-11-17"], "until": "2026-11-19"},
            ],
        }
    ]


def test_a_reason_without_dates_or_until_is_short(clinic: Clinic) -> None:
    clinic.practitioner("d1", weekdays=())
    result = search_slots(
        clinic.conn, now=NOW, treatment="checkup", before=MONDAY + timedelta(days=40)
    )
    beyond = reason_for(result, "d1", "beyond_horizon")
    assert beyond.as_dict() == {"reason": "beyond_horizon"}


def test_engine_never_reads_the_wall_clock() -> None:
    offenders = []
    for path in sorted((BACKEND / "engine").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        offenders += [
            f"{path.relative_to(BACKEND)}: {token}" for token in FORBIDDEN_CLOCK if token in text
        ]
    assert offenders == []


def test_the_engine_only_reads(tmp_path: Path, november_db: Path) -> None:
    path = tmp_path / "copy.db"
    shutil.copy(november_db, path)
    before = path.read_bytes()
    conn = sqlite3.connect(path)
    try:
        search_slots(conn, now=NOW, treatment="checkup", patient_id="p0001")
        check_slot(
            conn,
            now=NOW,
            practitioner_id="dent_1",
            treatment="checkup",
            start=local(TUESDAY, "11:00"),
        )
    finally:
        conn.close()
    assert path.read_bytes() == before
