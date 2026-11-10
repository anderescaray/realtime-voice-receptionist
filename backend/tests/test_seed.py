"""Tests for deterministic clinic seed."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.catalogue import TREATMENT_IDS, absences_for_anchor
from seed.timeutil import add_minutes_local, local_datetime, parse_local_date, to_utc_text
from seed.validate import occupancy, validate

_ISOLATED_SUNDAY_START = "2030-06-02T08:00:00Z"
_ISOLATED_SUNDAY_END = "2030-06-02T08:30:00Z"
_ISOLATED_SUNDAY_TOUCH = "2030-06-02T08:15:00Z"
_ISOLATED_SUNDAY_NEXT_START = "2030-06-02T08:30:00Z"
_ISOLATED_SUNDAY_NEXT_END = "2030-06-02T09:00:00Z"

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


def _insert_booked(
    conn: sqlite3.Connection,
    aid: str,
    *,
    patient_id: str,
    practitioner_id: str,
    start_utc: str,
    end_utc: str,
) -> None:
    conn.execute(
        """
        INSERT INTO appointments (
            appointment_id, patient_id, practitioner_id, treatment_id,
            start_utc, end_utc, status, same_day_urgent, created_by_call
        ) VALUES (?, ?, ?, 'checkup', ?, ?, 'booked', 0, NULL)
        """,
        (aid, patient_id, practitioner_id, start_utc, end_utc),
    )


def test_overlap_trigger_same_practitioner_different_patient_insert(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99001",
            patient_id="p0001",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            _insert_booked(
                conn,
                "a99002",
                patient_id="p0002",
                practitioner_id="dent_1",
                start_utc=_ISOLATED_SUNDAY_TOUCH,
                end_utc=_ISOLATED_SUNDAY_NEXT_END,
            )
    finally:
        conn.close()


def test_overlap_trigger_same_patient_different_practitioner_insert(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99003",
            patient_id="p0001",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            _insert_booked(
                conn,
                "a99004",
                patient_id="p0001",
                practitioner_id="dent_2",
                start_utc=_ISOLATED_SUNDAY_TOUCH,
                end_utc=_ISOLATED_SUNDAY_NEXT_END,
            )
    finally:
        conn.close()


def test_overlap_trigger_adjacent_insert_allowed(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99005",
            patient_id="p0001",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        _insert_booked(
            conn,
            "a99006",
            patient_id="p0002",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_NEXT_START,
            end_utc=_ISOLATED_SUNDAY_NEXT_END,
        )
        conn.commit()
    finally:
        conn.close()


def test_overlap_trigger_cancelled_row_insert_allowed(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, patient_id, practitioner_id, treatment_id,
                start_utc, end_utc, status, same_day_urgent, created_by_call
            ) VALUES ('a99007', 'p0003', 'dent_3', 'checkup', ?, ?, 'cancelled', 0, NULL)
            """,
            (_ISOLATED_SUNDAY_START, _ISOLATED_SUNDAY_END),
        )
        _insert_booked(
            conn,
            "a99008",
            patient_id="p0001",
            practitioner_id="dent_3",
            start_utc=_ISOLATED_SUNDAY_TOUCH,
            end_utc=_ISOLATED_SUNDAY_NEXT_END,
        )
        conn.commit()
    finally:
        conn.close()


def test_overlap_trigger_cancelled_to_booked_update(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99009",
            patient_id="p0001",
            practitioner_id="dent_4",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        conn.commit()
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, patient_id, practitioner_id, treatment_id,
                start_utc, end_utc, status, same_day_urgent, created_by_call
            ) VALUES ('a99010', 'p0002', 'dent_4', 'checkup', ?, ?, 'cancelled', 0, NULL)
            """,
            (_ISOLATED_SUNDAY_TOUCH, _ISOLATED_SUNDAY_NEXT_END),
        )
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            conn.execute(
                "UPDATE appointments SET status = 'booked' WHERE appointment_id = 'a99010'"
            )
    finally:
        conn.close()


def test_overlap_trigger_same_practitioner_different_patient_update(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99011",
            patient_id="p0001",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, patient_id, practitioner_id, treatment_id,
                start_utc, end_utc, status, same_day_urgent, created_by_call
            ) VALUES ('a99012', 'p0002', 'dent_2', 'checkup', ?, ?, 'booked', 0, NULL)
            """,
            (
                "2030-06-09T08:00:00Z",
                "2030-06-09T08:30:00Z",
            ),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            conn.execute(
                "UPDATE appointments SET practitioner_id = 'dent_1',"
                " start_utc = ?, end_utc = ? WHERE appointment_id = 'a99012'",
                (_ISOLATED_SUNDAY_TOUCH, _ISOLATED_SUNDAY_NEXT_END),
            )
    finally:
        conn.close()


def test_overlap_trigger_same_patient_different_practitioner_update(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        _insert_booked(
            conn,
            "a99013",
            patient_id="p0001",
            practitioner_id="dent_1",
            start_utc=_ISOLATED_SUNDAY_START,
            end_utc=_ISOLATED_SUNDAY_END,
        )
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, patient_id, practitioner_id, treatment_id,
                start_utc, end_utc, status, same_day_urgent, created_by_call
            ) VALUES ('a99014', 'p0002', 'dent_2', 'checkup', ?, ?, 'booked', 0, NULL)
            """,
            (
                "2030-06-09T09:00:00Z",
                "2030-06-09T09:30:00Z",
            ),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="overlap"):
            conn.execute(
                "UPDATE appointments SET patient_id = 'p0001',"
                " start_utc = ?, end_utc = ? WHERE appointment_id = 'a99014'",
                (_ISOLATED_SUNDAY_TOUCH, _ISOLATED_SUNDAY_NEXT_END),
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


@pytest.mark.parametrize(
    "anchor",
    ("2026-12-21", "2026-12-28", "2027-03-29"),
)
def test_fully_booked_fixture_anchors(tmp_path: Path, anchor: str) -> None:
    db = tmp_path / f"anchor-{anchor}.db"
    build_db(db, parse_local_date(anchor), DEFAULT_SEED)
    assert validate(db) == []
    conn = sqlite3.connect(db)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        fixtures = json.loads(meta["fixtures"])
        fb_date = fixtures["fully_booked"]["date"]
        closures = {r[0] for r in conn.execute("SELECT date FROM closures")}
        assert fb_date not in closures
    finally:
        conn.close()


def test_validate_import_independence() -> None:
    validate_py = Path(__file__).resolve().parent.parent / "seed" / "validate.py"
    tree = ast.parse(validate_py.read_text(encoding="utf-8"))
    forbidden = frozenset({"seed.catalogue", "seed.diary", "seed.people"})
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod in forbidden or any(mod.startswith(f + ".") for f in forbidden):
                pytest.fail(f"forbidden import in validate.py: {mod}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden:
                    pytest.fail(f"forbidden import in validate.py: {alias.name}")


def test_validate_missing_diary_start(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM meta WHERE key = 'diary_start'")
        conn.commit()
    finally:
        conn.close()
    errs = validate(db)
    assert any("diary_start" in e for e in errs)


def test_validate_invalid_fixtures_json(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE meta SET value = 'not json' WHERE key = 'fixtures'")
        conn.commit()
    finally:
        conn.close()
    errs = validate(db)
    assert any("fixtures" in e for e in errs)


def test_validate_site_row_required(tmp_path: Path) -> None:
    db = tmp_path / "clinic.db"
    build_db(db, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM site")
        conn.commit()
    finally:
        conn.close()
    errs = validate(db)
    assert any("site" in e.lower() for e in errs)


def test_madrid_local_to_utc_winter() -> None:
    dt = local_datetime(parse_local_date("2027-01-12"), "09:00")
    assert to_utc_text(dt) == "2027-01-12T08:00:00Z"


def test_madrid_local_to_utc_summer() -> None:
    dt = local_datetime(parse_local_date("2027-07-12"), "09:00")
    assert to_utc_text(dt) == "2027-07-12T07:00:00Z"


def test_dst_spring_appointment_sixty_utc_minutes() -> None:
    start = to_utc_text(local_datetime(parse_local_date("2027-03-26"), "19:00"))
    end = to_utc_text(add_minutes_local(parse_local_date("2027-03-26"), "19:00", 60))
    start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert int((end_dt - start_dt).total_seconds() // 60) == 60


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


def test_fully_booked_fixture_is_bookable_future_day(tmp_path: Path) -> None:
    """The fixture day is never 'today' or 'tomorrow' for a call on the anchor."""
    db = tmp_path / "fx.db"
    anchor = parse_local_date(DEFAULT_ANCHOR)
    assert build_db(db, anchor, DEFAULT_SEED) == []
    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT value FROM meta WHERE key = 'fixtures'").fetchone()[0]
    fixture_day = parse_local_date(json.loads(raw)["fully_booked"]["date"])
    assert (fixture_day - anchor).days >= 2
