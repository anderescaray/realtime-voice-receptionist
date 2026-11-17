"""Acceptance tests for store connection helpers and call staging."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.store import (
    ERROR_CODES,
    MADRID,
    Booking,
    Cancel,
    Move,
    NewPatient,
    Staging,
    StoreError,
    open_db,
    utc_text,
)

_FORBIDDEN_CLOCK = (
    "datetime.now(",
    "datetime.utcnow(",
    "date.today(",
    "time.time(",
)


def _aware_madrid(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


def _sample_booking(patient_id: str = "p1") -> Booking:
    start = _aware_madrid(2026, 11, 20, 10, 0)
    return Booking(
        patient_id=patient_id,
        practitioner_id="pr1",
        treatment="checkup",
        start=start,
        end=start + timedelta(minutes=30),
    )


def _sample_cancel() -> Cancel:
    start = _aware_madrid(2026, 11, 20, 10, 0)
    return Cancel(
        appointment_id="a1",
        patient_id="p1",
        seen_practitioner_id="pr1",
        seen_start=start,
        seen_end=start + timedelta(minutes=30),
    )


def _sample_move() -> Move:
    seen_start = _aware_madrid(2026, 11, 20, 10, 0)
    new_start = _aware_madrid(2026, 11, 21, 11, 0)
    return Move(
        appointment_id="a1",
        patient_id="p1",
        seen_practitioner_id="pr1",
        seen_start=seen_start,
        seen_end=seen_start + timedelta(minutes=30),
        practitioner_id="pr1",
        start=new_start,
        end=new_start + timedelta(minutes=30),
    )


def _sample_patient() -> NewPatient:
    return NewPatient(
        first="Ana",
        last1="García",
        last2=None,
        dob="1990-05-15",
        phone="+442079460001",
    )


def test_open_db_pragmas_and_row_factory(tmp_path: Path) -> None:
    db_path = tmp_path / "clinic.db"
    conn = open_db(db_path)
    try:
        assert conn.isolation_level is None
        assert conn.row_factory is sqlite3.Row
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal.lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()

    conn_short = open_db(tmp_path / "short.db", busy_timeout_ms=100)
    try:
        assert conn_short.execute("PRAGMA busy_timeout").fetchone()[0] == 100
    finally:
        conn_short.close()


def test_utc_text_madrid_and_naive() -> None:
    winter = _aware_madrid(2026, 11, 16, 10, 0)
    assert utc_text(winter) == "2026-11-16T09:00:00Z"

    summer = _aware_madrid(2027, 7, 1, 10, 0)
    assert utc_text(summer) == "2027-07-01T08:00:00Z"

    with pytest.raises(StoreError) as exc:
        utc_text(datetime(2026, 1, 1, 12, 0))
    assert exc.value.code == "bad_change"


def test_utc_text_accepts_utc_aware() -> None:
    moment = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    assert utc_text(moment) == "2026-06-01T12:00:00Z"


def test_store_error_codes() -> None:
    with pytest.raises(ValueError):
        StoreError("nope")
    for code in ERROR_CODES:
        err = StoreError(code)
        assert err.code == code


def test_booking_validation() -> None:
    start = _aware_madrid(2026, 11, 20, 10, 0)
    with pytest.raises(StoreError) as exc:
        Booking(
            patient_id="p1",
            practitioner_id="pr1",
            treatment="checkup",
            start=start,
            end=start,
        )
    assert exc.value.code == "bad_change"

    with pytest.raises(StoreError) as exc:
        Booking(
            patient_id="p1",
            practitioner_id="pr1",
            treatment="checkup",
            start=start,
            end=start - timedelta(minutes=15),
        )
    assert exc.value.code == "bad_change"

    with pytest.raises(StoreError) as exc:
        Booking(
            patient_id="p1",
            practitioner_id="pr1",
            treatment="checkup",
            start=datetime(2026, 11, 20, 10, 0),
            end=start + timedelta(minutes=30),
        )
    assert exc.value.code == "bad_change"


def test_new_patient_validation() -> None:
    base = dict(
        last1="López",
        last2=None,
        phone="+442079460002",
    )
    with pytest.raises(StoreError) as exc:
        NewPatient(first="", dob="1990-01-01", **base)
    assert exc.value.code == "bad_change"

    with pytest.raises(StoreError) as exc:
        NewPatient(first="Ana", dob="2026-02-30", **base)
    assert exc.value.code == "bad_change"

    with pytest.raises(StoreError) as exc:
        NewPatient(first="Ana", dob="16/11/1980", **base)
    assert exc.value.code == "bad_change"


def test_operation_keys_increment_per_call() -> None:
    staging = Staging("c1")
    pending = staging.stage_booking(_sample_booking())
    assert pending.operation_key == "c1:1"

    staging.discard()
    pending = staging.stage_cancel(_sample_cancel())
    assert pending.operation_key == "c1:2"

    staging.clear()
    pending = staging.stage_move(_sample_move())
    assert pending.operation_key == "c1:3"

    other = Staging("c2")
    assert other.stage_booking(_sample_booking()).operation_key == "c2:1"


def test_pending_exists_blocks_second_stage() -> None:
    staging = Staging("c1")
    first = staging.stage_booking(_sample_booking())
    with pytest.raises(StoreError) as exc:
        staging.stage_cancel(_sample_cancel())
    assert exc.value.code == "pending_exists"
    assert staging.pending is first


def test_register_book_extension() -> None:
    staging = Staging("c1")
    patient = _sample_patient()
    reg = staging.stage_register(patient, "p_new1")
    assert reg.kind == "register"
    assert reg.operation_key == "c1:1"

    extended = staging.stage_booking(_sample_booking(patient_id="p_new1"))
    assert extended.kind == "register_book"
    assert extended.operation_key == "c1:1"
    assert extended.new_patient == patient
    assert extended.provisional_patient_id == "p_new1"
    assert extended.booking is not None

    staging.discard()
    assert staging.pending is None
    next_book = staging.stage_booking(_sample_booking())
    assert next_book.operation_key == "c1:2"


def test_register_book_pending_exists_cases() -> None:
    staging = Staging("c1")
    staging.stage_register(_sample_patient(), "p_new1")

    with pytest.raises(StoreError) as exc:
        staging.stage_booking(_sample_booking(patient_id="p_other"))
    assert exc.value.code == "pending_exists"

    staging.stage_booking(_sample_booking(patient_id="p_new1"))
    with pytest.raises(StoreError) as exc:
        staging.stage_booking(_sample_booking(patient_id="p_new1"))
    assert exc.value.code == "pending_exists"


def test_discard_register_book_and_nothing_pending() -> None:
    staging = Staging("c1")
    staging.stage_register(_sample_patient(), "p_new1")
    staging.stage_booking(_sample_booking(patient_id="p_new1"))
    staging.discard()
    assert staging.pending is None

    with pytest.raises(StoreError) as exc:
        staging.discard()
    assert exc.value.code == "nothing_pending"

    with pytest.raises(StoreError) as exc:
        staging.clear()
    assert exc.value.code == "nothing_pending"


def test_store_py_has_no_wall_clock_reads() -> None:
    path = Path(__file__).resolve().parent.parent / "engine" / "store.py"
    text = path.read_text(encoding="utf-8")
    for token in _FORBIDDEN_CLOCK:
        assert token not in text, f"forbidden wall-clock use: {token}"
