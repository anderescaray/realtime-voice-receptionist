"""Acceptance tests for messages, review links, expiry listing, and purge."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.store import (
    MADRID,
    Booking,
    Cancel,
    NewPatient,
    PendingChange,
    Staging,
    StoreError,
    add_message,
    add_review_link,
    expired_calls,
    open_db,
    purge_call,
    save,
    utc_text,
)
from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.timeutil import parse_local_date

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MADRID)
SAVED_UTC = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
_MSG_ID_RE = re.compile(r"^m_[0-9a-f]{8}$")
_GOOD_HASH = "a" * 64


def _madrid(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "clinic.db"
    build_db(db_path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    return open_db(db_path)


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("patients", "appointments", "messages", "changes", "review_links")
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in tables
    }


def test_add_message(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    msg_id = add_message(
        conn,
        call_id="c1",
        name="Caller",
        phone="+34123456789",
        text="Please call back",
        urgent=True,
        saved_utc=SAVED_UTC,
    )
    assert _MSG_ID_RE.fullmatch(msg_id)
    row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (msg_id,)).fetchone()
    assert row["urgent"] == 1
    assert row["saved_utc"] == utc_text(SAVED_UTC)

    msg_id2 = add_message(
        conn,
        call_id="c1",
        name="Caller",
        phone="+34123456789",
        text="Not urgent",
        urgent=False,
        saved_utc=SAVED_UTC,
    )
    row2 = conn.execute("SELECT urgent FROM messages WHERE message_id = ?", (msg_id2,)).fetchone()
    assert row2["urgent"] == 0

    with pytest.raises(StoreError) as exc:
        add_message(
            conn,
            call_id="c1",
            name="Caller",
            phone="+34123456789",
            text="   ",
            urgent=False,
            saved_utc=SAVED_UTC,
        )
    assert exc.value.code == "bad_change"


def test_add_review_link_validation(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    expires = _madrid(2026, 11, 23, 10, 0)
    for bad in ("a" * 63, "A" * 64, "g" * 64):
        with pytest.raises(StoreError) as exc:
            add_review_link(conn, call_id="c1", token_sha256=bad, expires_utc=expires)
        assert exc.value.code == "bad_change"

    add_review_link(conn, call_id="c1", token_sha256=_GOOD_HASH, expires_utc=expires)
    with pytest.raises(StoreError) as exc2:
        add_review_link(conn, call_id="c1", token_sha256="b" * 64, expires_utc=expires)
    assert exc2.value.code == "bad_change"


def test_expired_calls_sorted(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    early = utc_text(_madrid(2026, 11, 15, 9, 0))
    exact = utc_text(NOW)
    late = utc_text(_madrid(2026, 11, 17, 9, 0))
    conn.execute(
        "INSERT INTO review_links (token_sha256, call_id, expires_utc) VALUES (?, ?, ?)",
        ("1" * 64, "call_z", early),
    )
    conn.execute(
        "INSERT INTO review_links (token_sha256, call_id, expires_utc) VALUES (?, ?, ?)",
        ("2" * 64, "call_a", exact),
    )
    conn.execute(
        "INSERT INTO review_links (token_sha256, call_id, expires_utc) VALUES (?, ?, ?)",
        ("3" * 64, "call_m", late),
    )
    assert expired_calls(conn, now=NOW) == ["call_a", "call_z"]


def test_purge_call_scenario(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    before = _table_counts(conn)
    add_review_link(
        conn,
        call_id="A",
        token_sha256="4" * 64,
        expires_utc=_madrid(2026, 11, 23, 10, 0),
    )

    staging_a = Staging("A")
    patient = NewPatient(
        first="Purga",
        last1="Uno",
        last2=None,
        dob="1982-03-03",
        phone="+34111000001",
    )
    staging_a.stage_register(patient, "p_new1")
    reg_book = staging_a.stage_booking(
        Booking(
            patient_id="p_new1",
            practitioner_id="dent_1",
            treatment="checkup",
            start=_madrid(2027, 1, 20, 10, 0),
            end=_madrid(2027, 1, 20, 10, 30),
        )
    )
    save(conn, reg_book, now=NOW, saved_utc=SAVED_UTC)

    save(
        conn,
        PendingChange(
            operation_key="A:2",
            call_id="A",
            kind="book",
            booking=Booking(
                patient_id="p0003",
                practitioner_id="dent_2",
                treatment="checkup",
                start=_madrid(2027, 1, 21, 11, 0),
                end=_madrid(2027, 1, 21, 11, 30),
            ),
        ),
        now=NOW,
        saved_utc=SAVED_UTC,
    )

    cancel_row = conn.execute(
        """
        SELECT appointment_id FROM appointments
        WHERE status = 'booked' AND created_by_call IS NULL
        LIMIT 1
        """
    ).fetchone()
    assert cancel_row is not None
    appt_id = str(cancel_row["appointment_id"])
    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?",
        (appt_id,),
    ).fetchone()
    start = datetime.fromisoformat(str(row["start_utc"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(row["end_utc"]).replace("Z", "+00:00"))
    save(
        conn,
        PendingChange(
            operation_key="A:3",
            call_id="A",
            kind="cancel",
            cancel=Cancel(
                appointment_id=appt_id,
                patient_id=str(row["patient_id"]),
                seen_practitioner_id=str(row["practitioner_id"]),
                seen_start=start,
                seen_end=end,
            ),
        ),
        now=NOW,
        saved_utc=SAVED_UTC,
    )

    add_message(
        conn,
        call_id="A",
        name="Mensaje",
        phone="+34111000002",
        text="Hola",
        urgent=False,
        saved_utc=SAVED_UTC,
    )

    save(
        conn,
        PendingChange(
            operation_key="B:1",
            call_id="B",
            kind="book",
            booking=Booking(
                patient_id="p0004",
                practitioner_id="dent_3",
                treatment="filling",
                start=_madrid(2027, 1, 22, 9, 0),
                end=_madrid(2027, 1, 22, 9, 45),
            ),
        ),
        now=NOW,
        saved_utc=SAVED_UTC,
    )
    add_message(
        conn,
        call_id="B",
        name="Bench",
        phone="+34111000003",
        text="Sin enlace",
        urgent=False,
        saved_utc=SAVED_UTC,
    )

    counts = purge_call(conn, "A")
    assert counts["review_links"] == 1
    assert counts["patients"] >= 1
    assert counts["messages"] >= 1
    assert counts["changes"] >= 3

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM patients WHERE created_by_call = 'A'",
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE created_by_call = 'A'",
        ).fetchone()[0]
        == 0
    )
    for table, column in (
        ("messages", "call_id"),
        ("changes", "call_id"),
        ("review_links", "call_id"),
    ):
        assert (
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = 'A'",
            ).fetchone()[0]
            == 0
        )

    cancelled = conn.execute(
        "SELECT status FROM appointments WHERE appointment_id = ?",
        (appt_id,),
    ).fetchone()
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"

    after_b = conn.execute("SELECT COUNT(*) FROM messages WHERE call_id = 'B'").fetchone()[0]
    assert after_b == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM appointments WHERE created_by_call = 'B'").fetchone()[0]
        == 1
    )
    assert int(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]) == before["patients"]


def test_purge_removes_later_booking_for_registered_patient(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    add_review_link(
        conn,
        call_id="A",
        token_sha256="5" * 64,
        expires_utc=_madrid(2026, 11, 23, 10, 0),
    )
    new_patient = NewPatient(
        first="Borrar",
        last1="Luego",
        last2=None,
        dob="1979-07-07",
        phone="+34111000004",
    )
    reg_only = save(
        conn,
        PendingChange(
            operation_key="A:1",
            call_id="A",
            kind="register",
            new_patient=new_patient,
            provisional_patient_id="p_new1",
        ),
        now=NOW,
        saved_utc=SAVED_UTC,
    )
    patient_id = reg_only.patient_id
    assert patient_id is not None

    save(
        conn,
        PendingChange(
            operation_key="C:1",
            call_id="C",
            kind="book",
            booking=Booking(
                patient_id=patient_id,
                practitioner_id="dent_1",
                treatment="checkup",
                start=_madrid(2027, 2, 5, 10, 0),
                end=_madrid(2027, 2, 5, 10, 30),
            ),
        ),
        now=NOW,
        saved_utc=SAVED_UTC,
    )

    purge_call(conn, "A")
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM patients WHERE patient_id = ?", (patient_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE patient_id = ?",
            (patient_id,),
        ).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT COUNT(*) FROM changes WHERE call_id = 'C'").fetchone()[0] == 1


def test_purge_without_review_link(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    before = _table_counts(conn)
    with pytest.raises(StoreError) as exc:
        purge_call(conn, "B")
    assert exc.value.code == "bad_change"
    assert exc.value.detail == "no review link"
    assert _table_counts(conn) == before


def test_second_purge_bad_change(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    add_review_link(
        conn,
        call_id="A",
        token_sha256="6" * 64,
        expires_utc=_madrid(2026, 11, 23, 10, 0),
    )
    add_message(
        conn,
        call_id="A",
        name="Uno",
        phone="+34111000005",
        text="x",
        urgent=False,
        saved_utc=SAVED_UTC,
    )
    purge_call(conn, "A")
    after_first = _table_counts(conn)
    with pytest.raises(StoreError) as exc:
        purge_call(conn, "A")
    assert exc.value.code == "bad_change"
    assert _table_counts(conn) == after_first
