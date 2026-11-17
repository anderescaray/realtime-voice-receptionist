"""Acceptance tests for persisting staged register and book changes."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.store import (
    MADRID,
    Booking,
    NewPatient,
    PendingChange,
    Staging,
    StoreError,
    open_db,
    save,
)
from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.timeutil import parse_local_date

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MADRID)
SAVED_UTC = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
_APPT_ID_RE = re.compile(r"^a_[0-9a-f]{8}$")


def _madrid(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "clinic.db"
    build_db(db_path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    return open_db(db_path)


def _pending_book(
    *,
    operation_key: str = "c1:1",
    call_id: str = "c1",
    patient_id: str = "p0001",
    practitioner_id: str = "dent_1",
    treatment: str = "checkup",
    start: datetime | None = None,
    minutes: int = 30,
) -> PendingChange:
    if start is None:
        start = _madrid(2027, 1, 11, 10, 0)
    end = start + timedelta(minutes=minutes)
    return PendingChange(
        operation_key=operation_key,
        call_id=call_id,
        kind="book",
        booking=Booking(
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            treatment=treatment,
            start=start,
            end=end,
        ),
    )


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    patients = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    appointments = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    changes = conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
    return int(patients), int(appointments), int(changes)


def test_book_seeded_patient(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    change = _pending_book()
    result = save(conn, change, now=NOW, saved_utc=SAVED_UTC)

    assert result.operation_key == "c1:1"
    assert result.kind == "book"
    assert result.patient_id == "p0001"
    assert result.same_day_urgent is False
    assert result.already_saved is False
    assert result.appointment_id is not None
    assert _APPT_ID_RE.fullmatch(result.appointment_id)

    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?",
        (result.appointment_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "booked"
    assert row["created_by_call"] == "c1"
    assert row["start_utc"] == "2027-01-11T09:00:00Z"
    assert row["end_utc"] == "2027-01-11T09:30:00Z"
    assert row["same_day_urgent"] == 0

    ch = conn.execute("SELECT * FROM changes WHERE operation_key = ?", ("c1:1",)).fetchone()
    assert ch is not None
    assert ch["action"] == "book"
    assert ch["call_id"] == "c1"
    assert ch["saved_utc"] == "2026-11-16T09:05:00Z"
    details = json.loads(ch["details"])
    appt = details["after"]["appointment"]
    assert details["before"] is None
    assert appt["appointment_id"] == result.appointment_id
    assert appt["patient_id"] == "p0001"
    assert appt["practitioner_id"] == "dent_1"
    assert appt["treatment_id"] == "checkup"
    assert appt["status"] == "booked"
    assert appt["same_day_urgent"] == 0
    assert result.change_ids == [int(ch["change_id"])]


def test_same_day_urgent_flags(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    free_morning = _madrid(2026, 11, 16, 13, 0)

    urgent_today = _pending_book(
        operation_key="c1:1",
        treatment="urgency",
        start=free_morning,
        minutes=30,
    )
    r1 = save(conn, urgent_today, now=NOW, saved_utc=SAVED_UTC)
    assert r1.same_day_urgent is True
    row = conn.execute(
        "SELECT same_day_urgent FROM appointments WHERE appointment_id = ?",
        (r1.appointment_id,),
    ).fetchone()
    assert row["same_day_urgent"] == 1

    urgent_tomorrow = _pending_book(
        operation_key="c2:1",
        call_id="c2",
        treatment="urgency",
        start=_madrid(2027, 1, 12, 10, 0),
    )
    r2 = save(conn, urgent_tomorrow, now=NOW, saved_utc=SAVED_UTC)
    assert r2.same_day_urgent is False

    checkup_today = _pending_book(
        operation_key="c3:1",
        call_id="c3",
        treatment="checkup",
        start=_madrid(2026, 11, 16, 14, 0),
    )
    r3 = save(conn, checkup_today, now=NOW, saved_utc=SAVED_UTC)
    assert r3.same_day_urgent is False

    late_now = _madrid(2026, 11, 16, 23, 30)
    after_midnight = _madrid(2026, 11, 17, 0, 30)
    urgent_next_calendar_day = _pending_book(
        operation_key="c4:1",
        call_id="c4",
        treatment="urgency",
        start=after_midnight,
    )
    r4 = save(conn, urgent_next_calendar_day, now=late_now, saved_utc=SAVED_UTC)
    assert r4.same_day_urgent is False


def test_register_mints_patient_id(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    patient = NewPatient(
        first="Nueva",
        last1="Paciente",
        last2=None,
        dob="1988-01-20",
        phone="+441234567890",
    )
    change = PendingChange(
        operation_key="c1:1",
        call_id="c1",
        kind="register",
        new_patient=patient,
        provisional_patient_id="p_new1",
    )
    result = save(conn, change, now=NOW, saved_utc=SAVED_UTC)
    assert result.patient_id == "p_0301"
    assert result.appointment_id is None
    assert result.change_ids

    prow = conn.execute("SELECT * FROM patients WHERE patient_id = ?", ("p_0301",)).fetchone()
    assert prow["created_by_call"] == "c1"
    assert prow["last_name2"] is None

    ch = conn.execute("SELECT * FROM changes WHERE operation_key = 'c1:1'").fetchone()
    assert ch["action"] == "register"

    change2 = PendingChange(
        operation_key="c2:1",
        call_id="c2",
        kind="register",
        new_patient=NewPatient(
            first="Otro",
            last1="Nuevo",
            last2="Segundo",
            dob="1995-06-01",
            phone="+441234567891",
        ),
        provisional_patient_id="p_new2",
    )
    result2 = save(conn, change2, now=NOW, saved_utc=SAVED_UTC)
    assert result2.patient_id == "p_0302"


def test_register_book_two_changes(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    staging = Staging("c1")
    patient = NewPatient(
        first="Combo",
        last1="Registro",
        last2=None,
        dob="1993-04-04",
        phone="+441234567892",
    )
    staging.stage_register(patient, "p_new1")
    start = _madrid(2027, 1, 12, 11, 0)
    pending = staging.stage_booking(
        Booking(
            patient_id="p_new1",
            practitioner_id="dent_2",
            treatment="checkup",
            start=start,
            end=start + timedelta(minutes=30),
        )
    )

    result = save(conn, pending, now=NOW, saved_utc=SAVED_UTC)
    assert result.kind == "register_book"
    assert result.patient_id is not None
    assert result.appointment_id is not None
    assert len(result.change_ids) == 2
    assert result.change_ids[0] < result.change_ids[1]

    appt = conn.execute(
        "SELECT patient_id FROM appointments WHERE appointment_id = ?",
        (result.appointment_id,),
    ).fetchone()
    assert appt["patient_id"] == result.patient_id

    reg_ch = conn.execute("SELECT * FROM changes WHERE operation_key = 'c1:1/register'").fetchone()
    book_ch = conn.execute("SELECT * FROM changes WHERE operation_key = 'c1:1'").fetchone()
    assert reg_ch["action"] == "register"
    assert book_ch["action"] == "book"
    assert result.change_ids == [int(reg_ch["change_id"]), int(book_ch["change_id"])]


def test_idempotent_save(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)

    book = _pending_book(operation_key="c1:1")
    first = save(conn, book, now=NOW, saved_utc=SAVED_UTC)
    before = _counts(conn)
    second = save(conn, book, now=NOW, saved_utc=SAVED_UTC)
    assert second.already_saved is True
    assert replace(second, already_saved=False) == first
    assert _counts(conn) == before

    reg = PendingChange(
        operation_key="c2:1",
        call_id="c2",
        kind="register",
        new_patient=NewPatient(
            first="Idem",
            last1="Reg",
            last2=None,
            dob="2000-01-01",
            phone="+441234567893",
        ),
        provisional_patient_id="p_new1",
    )
    r1 = save(conn, reg, now=NOW, saved_utc=SAVED_UTC)
    before = _counts(conn)
    r2 = save(conn, reg, now=NOW, saved_utc=SAVED_UTC)
    assert r2.already_saved is True
    assert replace(r2, already_saved=False) == r1
    assert _counts(conn) == before

    staging = Staging("c3")
    staging.stage_register(
        NewPatient(
            first="Idem",
            last1="Combo",
            last2="Dos",
            dob="2001-02-02",
            phone="+441234567894",
        ),
        "p_new1",
    )
    start = _madrid(2027, 1, 13, 9, 0)
    combo = staging.stage_booking(
        Booking(
            patient_id="p_new1",
            practitioner_id="dent_3",
            treatment="filling",
            start=start,
            end=start + timedelta(minutes=45),
        )
    )
    c1 = save(conn, combo, now=NOW, saved_utc=SAVED_UTC)
    before = _counts(conn)
    c2 = save(conn, combo, now=NOW, saved_utc=SAVED_UTC)
    assert c2.already_saved is True
    assert c2.change_ids == c1.change_ids
    assert c2.patient_id == c1.patient_id
    assert c2.appointment_id == c1.appointment_id
    assert _counts(conn) == before


def test_overlap_mapping(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    existing = conn.execute(
        """
        SELECT patient_id, practitioner_id, start_utc, end_utc, status
        FROM appointments WHERE appointment_id = 'a00369'
        """
    ).fetchone()
    assert existing["status"] == "booked"
    start = datetime.fromisoformat(existing["start_utc"].replace("Z", "+00:00"))

    slot_taken_change = _pending_book(
        operation_key="c1:1",
        patient_id="p0002",
        practitioner_id=existing["practitioner_id"],
        start=start,
        minutes=30,
    )
    before = _counts(conn)
    with pytest.raises(StoreError) as exc:
        save(conn, slot_taken_change, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "slot_taken"
    assert _counts(conn) == before
    assert conn.in_transaction is False

    patient_change = _pending_book(
        operation_key="c2:1",
        call_id="c2",
        patient_id=existing["patient_id"],
        practitioner_id="dent_3",
        start=start,
        minutes=30,
    )
    before = _counts(conn)
    with pytest.raises(StoreError) as exc2:
        save(conn, patient_change, now=NOW, saved_utc=SAVED_UTC)
    assert exc2.value.code == "patient_overlap"
    assert _counts(conn) == before

    both_change = _pending_book(
        operation_key="c3:1",
        call_id="c3",
        patient_id=existing["patient_id"],
        practitioner_id=existing["practitioner_id"],
        start=start,
        minutes=30,
    )
    with pytest.raises(StoreError) as exc3:
        save(conn, both_change, now=NOW, saved_utc=SAVED_UTC)
    assert exc3.value.code == "slot_taken"

    gap_start = datetime.fromisoformat("2026-11-16T09:00:00+00:00")
    touching = _pending_book(
        operation_key="c4:1",
        call_id="c4",
        patient_id="p0010",
        practitioner_id=existing["practitioner_id"],
        start=gap_start,
        minutes=15,
    )
    touching_result = save(conn, touching, now=NOW, saved_utc=SAVED_UTC)
    assert touching_result.appointment_id is not None

    cancelled = conn.execute(
        """
        SELECT practitioner_id, start_utc, end_utc FROM appointments
        WHERE appointment_id = 'a00124'
        """
    ).fetchone()
    c_start = datetime.fromisoformat(cancelled["start_utc"].replace("Z", "+00:00"))
    c_end = datetime.fromisoformat(cancelled["end_utc"].replace("Z", "+00:00"))
    over_cancelled = _pending_book(
        operation_key="c5:1",
        call_id="c5",
        patient_id="p0011",
        practitioner_id=cancelled["practitioner_id"],
        start=c_start,
        minutes=int((c_end - c_start).total_seconds() // 60),
    )
    over_cancel = save(conn, over_cancelled, now=NOW, saved_utc=SAVED_UTC)
    assert over_cancel.appointment_id is not None


def test_register_book_overlap_rolls_back_all(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    existing = conn.execute(
        """
        SELECT practitioner_id, start_utc, end_utc
        FROM appointments WHERE appointment_id = 'a00369'
        """
    ).fetchone()
    start = datetime.fromisoformat(existing["start_utc"].replace("Z", "+00:00"))

    staging = Staging("c1")
    staging.stage_register(
        NewPatient(
            first="Fallo",
            last1="Overlap",
            last2=None,
            dob="1994-08-08",
            phone="+441234567895",
        ),
        "p_new1",
    )
    pending = staging.stage_booking(
        Booking(
            patient_id="p_new1",
            practitioner_id=existing["practitioner_id"],
            treatment="checkup",
            start=start,
            end=start + timedelta(minutes=30),
        )
    )
    before = _counts(conn)
    with pytest.raises(StoreError) as exc:
        save(conn, pending, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "slot_taken"
    assert _counts(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0


def test_already_registered(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    seeded = conn.execute("SELECT * FROM patients WHERE patient_id = 'p0296'").fetchone()
    duplicate = PendingChange(
        operation_key="c1:1",
        call_id="c1",
        kind="register",
        new_patient=NewPatient(
            first=seeded["first_name"],
            last1=seeded["last_name1"],
            last2=seeded["last_name2"],
            dob=seeded["dob"],
            phone="+449999999999",
        ),
        provisional_patient_id="p_new1",
    )
    with pytest.raises(StoreError) as exc:
        save(conn, duplicate, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "already_registered"

    different_dob = replace(
        duplicate,
        new_patient=NewPatient(
            first=seeded["first_name"],
            last1=seeded["last_name1"],
            last2=seeded["last_name2"],
            dob="1990-01-01",
            phone="+449999999998",
        ),
    )
    saved = save(conn, different_dob, now=NOW, saved_utc=SAVED_UTC)
    assert saved.patient_id == "p_0301"


def test_store_unavailable_busy(tmp_path: Path) -> None:
    db_path = tmp_path / "clinic.db"
    build_db(db_path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    holder = open_db(db_path)
    saver = open_db(db_path, busy_timeout_ms=100)
    holder.execute("BEGIN IMMEDIATE")
    change = _pending_book(operation_key="c1:1")
    before = _counts(saver)
    with pytest.raises(StoreError) as exc:
        save(saver, change, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "store_unavailable"
    assert _counts(saver) == before
    holder.execute("ROLLBACK")
    result = save(saver, change, now=NOW, saved_utc=SAVED_UTC)
    assert result.appointment_id is not None


def test_bad_change_cases(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    bad_patient = _pending_book(operation_key="c1:1", patient_id="p9999")
    before = _counts(conn)
    with pytest.raises(StoreError) as exc:
        save(conn, bad_patient, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "bad_change"
    assert _counts(conn) == before
    assert conn.in_transaction is False

    move = PendingChange(operation_key="c2:1", call_id="c2", kind="move")
    with pytest.raises(StoreError) as exc2:
        save(conn, move, now=NOW, saved_utc=SAVED_UTC)
    assert exc2.value.code == "bad_change"

    cancel = PendingChange(operation_key="c3:1", call_id="c3", kind="cancel")
    with pytest.raises(StoreError) as exc3:
        save(conn, cancel, now=NOW, saved_utc=SAVED_UTC)
    assert exc3.value.code == "bad_change"

    naive_book = _pending_book(operation_key="c4:1", call_id="c4")
    with pytest.raises(StoreError) as exc4:
        save(conn, naive_book, now=datetime(2026, 11, 16, 10, 0), saved_utc=SAVED_UTC)
    assert exc4.value.code == "bad_change"


def test_connection_usable_after_store_error(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    existing = conn.execute(
        "SELECT practitioner_id, start_utc FROM appointments WHERE appointment_id = 'a00369'"
    ).fetchone()
    start = datetime.fromisoformat(existing["start_utc"].replace("Z", "+00:00"))
    clash = _pending_book(
        operation_key="c1:1",
        patient_id="p0003",
        practitioner_id=existing["practitioner_id"],
        start=start,
    )
    with pytest.raises(StoreError):
        save(conn, clash, now=NOW, saved_utc=SAVED_UTC)
    assert conn.in_transaction is False

    ok = _pending_book(
        operation_key="c1:2",
        start=_madrid(2027, 2, 1, 10, 0),
    )
    assert save(conn, ok, now=NOW, saved_utc=SAVED_UTC).appointment_id is not None
