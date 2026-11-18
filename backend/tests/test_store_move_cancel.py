"""Acceptance tests for persisting staged move and cancel changes."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.store import (
    MADRID,
    Booking,
    Cancel,
    Move,
    PendingChange,
    StoreError,
    open_db,
    save,
    utc_text,
)
from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.timeutil import parse_local_date

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MADRID)
SAVED_UTC = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)


def _madrid(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


def _utc_row(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "clinic.db"
    build_db(db_path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    return open_db(db_path)


def _booked_row(conn: sqlite3.Connection, appointment_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "booked"
    return row


def _seen_from_row(row: sqlite3.Row) -> tuple[str, str, datetime, datetime]:
    return (
        str(row["patient_id"]),
        str(row["practitioner_id"]),
        _utc_row(str(row["start_utc"])),
        _utc_row(str(row["end_utc"])),
    )


def _pending_move(
    conn: sqlite3.Connection,
    appointment_id: str,
    *,
    operation_key: str = "c1:1",
    call_id: str = "c1",
    practitioner_id: str | None = None,
    start: datetime | None = None,
    minutes: int | None = None,
    seen_override: dict[str, object] | None = None,
) -> PendingChange:
    row = _booked_row(conn, appointment_id)
    patient_id, seen_pr, seen_start, seen_end = _seen_from_row(row)
    if seen_override:
        patient_id = str(seen_override.get("patient_id", patient_id))
        seen_pr = str(seen_override.get("seen_practitioner_id", seen_pr))
        seen_start = seen_override.get("seen_start", seen_start)  # type: ignore[assignment]
        seen_end = seen_override.get("seen_end", seen_end)  # type: ignore[assignment]
    if minutes is None:
        minutes = int((seen_end - seen_start).total_seconds() // 60)
    if start is None:
        start = _madrid(2027, 1, 11, 10, 0)
    end = start + timedelta(minutes=minutes)
    new_pr = practitioner_id if practitioner_id is not None else seen_pr
    return PendingChange(
        operation_key=operation_key,
        call_id=call_id,
        kind="move",
        move=Move(
            appointment_id=appointment_id,
            patient_id=patient_id,
            seen_practitioner_id=seen_pr,
            seen_start=seen_start,
            seen_end=seen_end,
            practitioner_id=new_pr,
            start=start,
            end=end,
        ),
    )


def _pending_cancel(
    conn: sqlite3.Connection,
    appointment_id: str,
    *,
    operation_key: str = "c1:1",
    call_id: str = "c1",
    seen_override: dict[str, object] | None = None,
) -> PendingChange:
    row = _booked_row(conn, appointment_id)
    patient_id, seen_pr, seen_start, seen_end = _seen_from_row(row)
    if seen_override:
        patient_id = str(seen_override.get("patient_id", patient_id))
        seen_pr = str(seen_override.get("seen_practitioner_id", seen_pr))
        seen_start = seen_override.get("seen_start", seen_start)  # type: ignore[assignment]
        seen_end = seen_override.get("seen_end", seen_end)  # type: ignore[assignment]
    return PendingChange(
        operation_key=operation_key,
        call_id=call_id,
        kind="cancel",
        cancel=Cancel(
            appointment_id=appointment_id,
            patient_id=patient_id,
            seen_practitioner_id=seen_pr,
            seen_start=seen_start,
            seen_end=seen_end,
        ),
    )


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    patients = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    appointments = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    changes = conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
    return int(patients), int(appointments), int(changes)


def test_move_updates_row_and_changes(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    before_appts = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    appointment_id = "a00369"
    target = _madrid(2027, 1, 11, 10, 0)
    change = _pending_move(conn, appointment_id, practitioner_id="dent_2", start=target)
    result = save(conn, change, now=NOW, saved_utc=SAVED_UTC)

    assert result.kind == "move"
    assert result.appointment_id == appointment_id
    assert result.already_saved is False
    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    assert row["practitioner_id"] == "dent_2"
    assert row["start_utc"] == "2027-01-11T09:00:00Z"
    assert row["end_utc"] == "2027-01-11T09:30:00Z"
    assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == before_appts

    ch = conn.execute("SELECT * FROM changes WHERE operation_key = 'c1:1'").fetchone()
    assert ch is not None
    assert ch["action"] == "move"
    details = json.loads(ch["details"])
    assert (
        details["before"]["appointment"]["start_utc"]
        != details["after"]["appointment"]["start_utc"]
    )
    assert details["after"]["appointment"]["practitioner_id"] == "dent_2"


def test_move_overlap_errors(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    blocking = _booked_row(conn, "a00369")
    block_start = _utc_row(str(blocking["start_utc"]))
    mover = conn.execute(
        """
        SELECT appointment_id, patient_id FROM appointments
        WHERE status = 'booked' AND appointment_id <> 'a00369'
          AND patient_id <> ?
        LIMIT 1
        """,
        (blocking["patient_id"],),
    ).fetchone()
    assert mover is not None

    slot_taken = _pending_move(
        conn,
        str(mover["appointment_id"]),
        operation_key="c1:1",
        practitioner_id=str(blocking["practitioner_id"]),
        start=block_start,
    )
    before = _counts(conn)
    with pytest.raises(StoreError) as exc:
        save(conn, slot_taken, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "slot_taken"
    assert _counts(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0

    overlap_appt = conn.execute(
        "SELECT start_utc, end_utc FROM appointments WHERE appointment_id = 'a00097'",
    ).fetchone()
    assert overlap_appt is not None
    overlap_start = _utc_row(str(overlap_appt["start_utc"]))
    patient_overlap = _pending_move(
        conn,
        "a00369",
        operation_key="c2:1",
        call_id="c2",
        practitioner_id="dent_3",
        start=overlap_start,
        minutes=30,
    )
    before = _counts(conn)
    with pytest.raises(StoreError) as exc2:
        save(conn, patient_overlap, now=NOW, saved_utc=SAVED_UTC)
    assert exc2.value.code == "patient_overlap"
    assert _counts(conn) == before


def test_move_self_overlap_allowed(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    appointment_id = "a00010"
    row = _booked_row(conn, appointment_id)
    seen_start = _utc_row(str(row["start_utc"]))
    seen_end = _utc_row(str(row["end_utc"]))
    new_start = seen_start + timedelta(minutes=15)
    change = _pending_move(conn, appointment_id, start=new_start)
    result = save(conn, change, now=NOW, saved_utc=SAVED_UTC)
    assert result.appointment_id == appointment_id
    updated = conn.execute(
        "SELECT start_utc, end_utc FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    assert updated["start_utc"] == utc_text(new_start)
    assert updated["end_utc"] == utc_text(seen_end + timedelta(minutes=15))


def test_stale_view_rejected(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)

    row_a = _booked_row(conn, "a00369")
    pid_a, pr_a, start_a, end_a = _seen_from_row(row_a)
    conn.execute("UPDATE appointments SET status = 'cancelled' WHERE appointment_id = 'a00369'")
    with pytest.raises(StoreError) as exc:
        save(
            conn,
            PendingChange(
                operation_key="stale:1",
                call_id="stale",
                kind="cancel",
                cancel=Cancel(
                    appointment_id="a00369",
                    patient_id=pid_a,
                    seen_practitioner_id=pr_a,
                    seen_start=start_a,
                    seen_end=end_a,
                ),
            ),
            now=NOW,
            saved_utc=SAVED_UTC,
        )
    assert exc.value.code == "appointment_not_seen"
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0

    row_b = _booked_row(conn, "a00001")
    pid_b, pr_b, start_b, end_b = _seen_from_row(row_b)
    conn.execute(
        """
        UPDATE appointments
        SET start_utc = '2027-02-01T09:00:00Z', end_utc = '2027-02-01T09:30:00Z'
        WHERE appointment_id = 'a00001'
        """
    )
    with pytest.raises(StoreError) as exc2:
        save(
            conn,
            PendingChange(
                operation_key="stale:2",
                call_id="stale",
                kind="cancel",
                cancel=Cancel(
                    appointment_id="a00001",
                    patient_id=pid_b,
                    seen_practitioner_id=pr_b,
                    seen_start=start_b,
                    seen_end=end_b,
                ),
            ),
            now=NOW,
            saved_utc=SAVED_UTC,
        )
    assert exc2.value.code == "appointment_not_seen"

    wrong_patient = _pending_cancel(
        conn,
        "a00010",
        operation_key="stale:3",
        seen_override={"patient_id": "p0001"},
    )
    with pytest.raises(StoreError) as exc3:
        save(conn, wrong_patient, now=NOW, saved_utc=SAVED_UTC)
    assert exc3.value.code == "appointment_not_seen"

    row_c = _booked_row(conn, "a00010")
    pid_c, pr_c, start_c, end_c = _seen_from_row(row_c)
    with pytest.raises(StoreError) as exc4:
        save(
            conn,
            PendingChange(
                operation_key="stale:4",
                call_id="stale",
                kind="cancel",
                cancel=Cancel(
                    appointment_id="a_no_such",
                    patient_id=pid_c,
                    seen_practitioner_id=pr_c,
                    seen_start=start_c,
                    seen_end=end_c,
                ),
            ),
            now=NOW,
            saved_utc=SAVED_UTC,
        )
    assert exc4.value.code == "appointment_not_seen"


def test_cancel_frees_slot(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    appointment_id = "a00372"
    change = _pending_cancel(conn, appointment_id, operation_key="c_cancel:1", call_id="c_cancel")
    result = save(conn, change, now=NOW, saved_utc=SAVED_UTC)
    assert result.kind == "cancel"
    updated = conn.execute(
        "SELECT status FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    assert updated["status"] == "cancelled"
    ch = conn.execute(
        "SELECT action, details FROM changes WHERE operation_key = ?",
        ("c_cancel:1",),
    ).fetchone()
    assert ch is not None
    assert ch["action"] == "cancel"
    details = json.loads(ch["details"])
    assert details["before"]["appointment"]["status"] == "booked"
    assert details["after"]["appointment"]["status"] == "cancelled"

    row = conn.execute(
        "SELECT practitioner_id, start_utc, end_utc FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    start = _utc_row(str(row["start_utc"]))
    end = _utc_row(str(row["end_utc"]))
    book = PendingChange(
        operation_key="c2:1",
        call_id="c2",
        kind="book",
        booking=Booking(
            patient_id="p0002",
            practitioner_id=str(row["practitioner_id"]),
            treatment="checkup",
            start=start,
            end=end,
        ),
    )
    booked = save(conn, book, now=NOW, saved_utc=SAVED_UTC)
    assert booked.appointment_id is not None


def test_move_cancel_idempotent(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    move = _pending_move(conn, "a00369", start=_madrid(2027, 1, 12, 11, 0))
    first = save(conn, move, now=NOW, saved_utc=SAVED_UTC)
    before = _counts(conn)
    second = save(conn, move, now=NOW, saved_utc=SAVED_UTC)
    assert second.already_saved is True
    assert second.change_ids == first.change_ids
    assert second.appointment_id == first.appointment_id
    assert _counts(conn) == before

    cancel_appt = conn.execute(
        """
        SELECT appointment_id FROM appointments
        WHERE status = 'booked' AND appointment_id <> 'a00369'
        LIMIT 1
        """
    ).fetchone()
    assert cancel_appt is not None
    cancel_change = _pending_cancel(
        conn,
        str(cancel_appt["appointment_id"]),
        operation_key="c4:1",
        call_id="c4",
    )
    c1 = save(conn, cancel_change, now=NOW, saved_utc=SAVED_UTC)
    before = _counts(conn)
    c2 = save(conn, cancel_change, now=NOW, saved_utc=SAVED_UTC)
    assert c2.already_saved is True
    assert c2.change_ids == c1.change_ids
    assert _counts(conn) == before


def test_move_same_day_urgent_flag(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    book = PendingChange(
        operation_key="c1:1",
        call_id="c1",
        kind="book",
        booking=Booking(
            patient_id="p0005",
            practitioner_id="dent_1",
            treatment="urgency",
            start=_madrid(2027, 1, 15, 10, 0),
            end=_madrid(2027, 1, 15, 10, 30),
        ),
    )
    booked = save(conn, book, now=NOW, saved_utc=SAVED_UTC)
    assert booked.appointment_id is not None

    move_today = _pending_move(
        conn,
        booked.appointment_id,
        operation_key="c1:2",
        call_id="c1",
        start=_madrid(2026, 11, 16, 13, 0),
    )
    r1 = save(conn, move_today, now=NOW, saved_utc=SAVED_UTC)
    assert r1.same_day_urgent is True
    row = conn.execute(
        "SELECT same_day_urgent FROM appointments WHERE appointment_id = ?",
        (booked.appointment_id,),
    ).fetchone()
    assert row["same_day_urgent"] == 1

    conn.execute(
        """
        UPDATE appointments
        SET status = 'booked', start_utc = ?, end_utc = ?
        WHERE appointment_id = ?
        """,
        (
            "2027-01-15T09:00:00Z",
            "2027-01-15T09:30:00Z",
            booked.appointment_id,
        ),
    )
    move_tomorrow = _pending_move(
        conn,
        booked.appointment_id,
        operation_key="c1:3",
        call_id="c1",
        start=_madrid(2026, 11, 17, 14, 0),
    )
    r2 = save(conn, move_tomorrow, now=NOW, saved_utc=SAVED_UTC)
    assert r2.same_day_urgent is False


def test_cancelling_a_same_day_urgency_never_flags_the_call(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    book = PendingChange(
        operation_key="c1:1",
        call_id="c1",
        kind="book",
        booking=Booking(
            patient_id="p0005",
            practitioner_id="dent_1",
            treatment="urgency",
            start=_madrid(2026, 11, 16, 13, 0),
            end=_madrid(2026, 11, 16, 13, 30),
        ),
    )
    booked = save(conn, book, now=NOW, saved_utc=SAVED_UTC)
    assert booked.same_day_urgent is True
    assert booked.appointment_id is not None
    cancel = _pending_cancel(conn, booked.appointment_id, operation_key="c2:1", call_id="c2")
    result = save(conn, cancel, now=NOW, saved_utc=SAVED_UTC)
    assert result.same_day_urgent is False
    retry = save(conn, cancel, now=NOW, saved_utc=SAVED_UTC)
    assert retry.already_saved is True
    assert retry.same_day_urgent is False
    assert {**vars(retry), "already_saved": False} == vars(result)


def test_store_unavailable_on_move(tmp_path: Path) -> None:
    db_path = tmp_path / "clinic.db"
    build_db(db_path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    holder = open_db(db_path)
    saver = open_db(db_path, busy_timeout_ms=100)
    holder.execute("BEGIN IMMEDIATE")
    change = _pending_move(saver, "a00369", start=_madrid(2027, 1, 14, 9, 0))
    before = _counts(saver)
    with pytest.raises(StoreError) as exc:
        save(saver, change, now=NOW, saved_utc=SAVED_UTC)
    assert exc.value.code == "store_unavailable"
    assert _counts(saver) == before
    holder.execute("ROLLBACK")
