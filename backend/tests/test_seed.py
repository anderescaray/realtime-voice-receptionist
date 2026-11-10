"""Tests for deterministic clinic seed."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.catalogue import TREATMENT_IDS, absences_for_anchor
from seed.timeutil import parse_local_date
from seed.validate import occupancy, validate

_FORBIDDEN_CLOCK = (
    "datetime.now(",
    "datetime.utcnow(",
    "date.today(",
    "time.time(",
)


def _scan_no_wall_clock() -> list[str]:
    root = Path(__file__).resolve().parent.parent
    hits: list[str] = []
    for pattern in ("seed/*.py", "engine/*"):
        for path in sorted(root.glob(pattern)):
            if path.suffix != ".py" and path.suffix != ".sql":
                continue
            text = path.read_text(encoding="utf-8")
            for token in _FORBIDDEN_CLOCK:
                if token in text:
                    hits.append(f"{path.relative_to(root)}: {token}")
    return hits


def test_build_deterministic(tmp_path: Path) -> None:
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    anchor = parse_local_date(DEFAULT_ANCHOR)
    assert build_db(db_a, anchor, DEFAULT_SEED) == []
    assert build_db(db_b, anchor, DEFAULT_SEED) == []

    conn_a = sqlite3.connect(db_a)
    conn_b = sqlite3.connect(db_b)
    try:
        dump_a = "\n".join(conn_a.iterdump())
        dump_b = "\n".join(conn_b.iterdump())
    finally:
        conn_a.close()
        conn_b.close()

    assert dump_a == dump_b
    assert (
        hashlib.sha256(db_a.read_bytes()).hexdigest()
        == hashlib.sha256(db_b.read_bytes()).hexdigest()
    )


def test_cli_build_identical_across_processes(tmp_path: Path) -> None:
    """`make seed` runs the CLI; two fresh processes with different hash seeds and
    host time zones must still write byte-identical files."""
    backend = Path(__file__).resolve().parent.parent
    digests = []
    for hash_seed, host_tz in (("1", "UTC"), ("987", "America/New_York")):
        db = tmp_path / f"cli-{hash_seed}.db"
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "TZ": host_tz}
        subprocess.run(
            [sys.executable, "-m", "seed.build", "--db", str(db)],
            cwd=backend,
            env=env,
            check=True,
            capture_output=True,
        )
        digests.append(hashlib.sha256(db.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_different_seed_changes_patients(tmp_path: Path) -> None:
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    anchor = parse_local_date(DEFAULT_ANCHOR)
    build_db(db_a, anchor, DEFAULT_SEED)
    build_db(db_b, anchor, DEFAULT_SEED + 1)
    conn_a = sqlite3.connect(db_a)
    conn_b = sqlite3.connect(db_b)
    try:
        pa = conn_a.execute(
            "SELECT first_name, last_name1, dob FROM patients ORDER BY patient_id"
        ).fetchall()
        pb = conn_b.execute(
            "SELECT first_name, last_name1, dob FROM patients ORDER BY patient_id"
        ).fetchall()
    finally:
        conn_a.close()
        conn_b.close()
    assert pa != pb


def test_validate_fresh_build(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    assert validate(db) == []


def test_validate_closure_day(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    db2 = tmp_path / "broken.db"
    shutil.copy(db, db2)
    conn = sqlite3.connect(db2)
    try:
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, patient_id, practitioner_id, treatment_id,
                start_utc, end_utc, status, same_day_urgent, created_by_call
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "a99999",
                "p0001",
                "dent_1",
                "checkup",
                "2026-11-09T09:00:00Z",
                "2026-11-09T09:30:00Z",
                "booked",
                0,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    errs = validate(db2)
    assert any("closure" in e for e in errs)


def test_validate_non_fictional_phone(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE patients SET phone = '+34600111222' WHERE patient_id = 'p0001'")
        conn.commit()
    finally:
        conn.close()
    errs = validate(db)
    assert any("non-fictional" in e for e in errs)


def test_overlap_trigger_insert(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT start_utc, end_utc, practitioner_id, patient_id FROM appointments"
            " WHERE status = 'booked' LIMIT 1"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            conn.execute(
                """
                INSERT INTO appointments (
                    appointment_id, patient_id, practitioner_id, treatment_id,
                    start_utc, end_utc, status, same_day_urgent, created_by_call
                ) VALUES (?, ?, ?, ?, ?, ?, 'booked', 0, NULL)
                """,
                (
                    "a99998",
                    row[3],
                    row[2],
                    "checkup",
                    row[0],
                    row[1],
                ),
            )
    finally:
        conn.close()


def test_overlap_trigger_update(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT appointment_id, start_utc, end_utc, practitioner_id FROM appointments"
            " WHERE status = 'booked' LIMIT 2"
        ).fetchall()
        aid, _, _, _ = rows[0]
        _, start_b, end_b, pr_b = rows[1]
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            conn.execute(
                "UPDATE appointments SET start_utc = ?, end_utc = ?, practitioner_id = ?"
                " WHERE appointment_id = ?",
                (start_b, end_b, pr_b, aid),
            )
    finally:
        conn.close()


def test_treatment_vocab_and_ortho_price(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT treatment_id, minutes, price_eur FROM treatments").fetchall()
        ids = {r[0] for r in rows}
        assert ids == set(TREATMENT_IDS)
        for tid, mins, price in rows:
            assert mins % 15 == 0
            if tid == "ortho_consult":
                assert price is None
    finally:
        conn.close()


def test_absences_shift_with_anchor(tmp_path: Path) -> None:
    a1 = parse_local_date("2026-11-16")
    a2 = parse_local_date("2026-11-23")
    leave1 = next(a for a in absences_for_anchor(a1) if a["reason"] == "annual_leave")
    leave2 = next(a for a in absences_for_anchor(a2) if a["reason"] == "annual_leave")
    d1 = parse_local_date(leave1["start_date"])
    d2 = parse_local_date(leave2["start_date"])
    assert (d2 - d1).days == 7


def test_non_monday_anchor_refused() -> None:
    with pytest.raises(SystemExit):
        from seed.build import _monday_anchor

        _monday_anchor("2026-11-17")


def test_journal_mode_wal(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()


def test_seed_sources_no_wall_clock() -> None:
    assert _scan_no_wall_clock() == []


def test_cancelled_share_and_urgency_bounds(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        cancelled = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE status = 'cancelled'"
        ).fetchone()[0]
        share = cancelled / total
        assert 0.04 <= share <= 0.07

        anchor = parse_local_date(DEFAULT_ANCHOR)
        future_urgency = conn.execute(
            """
            SELECT COUNT(*) FROM appointments
            WHERE treatment_id = 'urgency' AND start_utc >= ?
            """,
            (anchor.isoformat() + "T00:00:00Z",),
        ).fetchone()[0]
        assert future_urgency == 0
    finally:
        conn.close()


def test_occupancy_falloff_and_week1_band(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    occ = occupancy(db)
    dentists = ("dent_1", "dent_2", "dent_3", "dent_4")
    for pid in dentists:
        b = occ[pid]
        assert b["week1"] > b["week2"] > b["week4"]
        assert 0.55 <= b["week1"] <= 0.80


def test_validate_malformed_utc(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    db2 = tmp_path / "bad_utc.db"
    shutil.copy(db, db2)
    conn = sqlite3.connect(db2)
    try:
        conn.execute(
            "UPDATE appointments SET start_utc = ?, end_utc = ? WHERE appointment_id = ?",
            ("2026-11-17T9:00:00Z", "2026-11-17T9:30:00Z", "a00001"),
        )
        conn.commit()
    finally:
        conn.close()
    errs = validate(db2)
    assert any("malformed UTC" in e for e in errs)


def test_future_booked_cap_per_patient(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        anchor = parse_local_date(DEFAULT_ANCHOR)
        rows = conn.execute(
            """
            SELECT patient_id, COUNT(*) AS n FROM appointments
            WHERE status = 'booked' AND start_utc >= ?
            GROUP BY patient_id HAVING n > 3
            """,
            (anchor.isoformat() + "T00:00:00Z",),
        ).fetchall()
        assert rows == []
    finally:
        conn.close()
