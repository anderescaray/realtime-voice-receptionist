"""Concurrent save races against a shared on-disk clinic database."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from engine.store import (
    MADRID,
    Booking,
    Cancel,
    Move,
    NewPatient,
    PendingChange,
    SaveResult,
    Staging,
    StoreError,
    open_db,
    save,
    utc_text,
)
from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.timeutil import parse_local_date

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=MADRID)
SAVED_UTC = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
JOIN_TIMEOUT_S = 20.0
HOLD_LOCK_S = 0.5


def _madrid(
    year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


def _utc_row(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _wal_sidecar(path: Path) -> Path:
    return Path(f"{path}-wal")


@pytest.fixture(scope="module")
def db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("store_concurrency_template")
    path = base / "template.db"
    build_db(path, parse_local_date(DEFAULT_ANCHOR), DEFAULT_SEED)
    conn = open_db(path)
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    assert not _wal_sidecar(path).exists()
    return path


@pytest.fixture
def db_path(db_template: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "clinic.db"
    assert not _wal_sidecar(db_template).exists()
    shutil.copyfile(db_template, dest)
    return dest


def _pending_book(
    *,
    operation_key: str,
    call_id: str,
    patient_id: str,
    practitioner_id: str = "dent_1",
    treatment: str = "checkup",
    start: datetime,
    minutes: int = 30,
) -> PendingChange:
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


def _treatment_for(conn: sqlite3.Connection, practitioner_id: str) -> str:
    row = conn.execute(
        "SELECT treatment_id FROM practitioner_treatments WHERE practitioner_id = ? LIMIT 1",
        (practitioner_id,),
    ).fetchone()
    assert row is not None
    return str(row["treatment_id"])


@dataclass
class Competitor:
    pending: PendingChange
    busy_timeout_ms: int = 5000


@dataclass
class ThreadOutcome:
    kind: Literal["ok", "store_error", "other"]
    result: SaveResult | None = None
    code: str | None = None
    exc: BaseException | None = None


def _race(db_path: Path, competitors: list[Competitor]) -> list[ThreadOutcome]:
    n = len(competitors)
    barrier = threading.Barrier(n)
    outcomes: list[ThreadOutcome | None] = [None] * n

    def worker(idx: int, comp: Competitor) -> None:
        try:
            conn = open_db(db_path, busy_timeout_ms=comp.busy_timeout_ms)
            try:
                barrier.wait(timeout=JOIN_TIMEOUT_S)
                try:
                    result = save(conn, comp.pending, now=NOW, saved_utc=SAVED_UTC)
                    outcomes[idx] = ThreadOutcome(kind="ok", result=result)
                except StoreError as exc:
                    outcomes[idx] = ThreadOutcome(kind="store_error", code=exc.code)
            finally:
                conn.close()
        except BaseException as exc:
            outcomes[idx] = ThreadOutcome(kind="other", exc=exc)

    threads = [
        threading.Thread(target=worker, args=(i, comp), name=f"race-{i}")
        for i, comp in enumerate(competitors)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_S)
        assert not thread.is_alive(), f"thread {thread.name} did not finish"

    assert all(o is not None for o in outcomes)
    return [o for o in outcomes if o is not None]


def _assert_no_unexpected(outcomes: list[ThreadOutcome]) -> None:
    for outcome in outcomes:
        if outcome.kind == "other":
            raise outcome.exc  # type: ignore[misc]


def _count_booked_at(
    conn: sqlite3.Connection, practitioner_id: str, start: datetime, end: datetime
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM appointments
        WHERE practitioner_id = ? AND status = 'booked'
          AND start_utc = ? AND end_utc = ?
        """,
        (practitioner_id, utc_text(start), utc_text(end)),
    ).fetchone()
    return int(row["n"])


def test_two_callers_same_slot(db_path: Path) -> None:
    slot = _madrid(2027, 1, 11, 10, 0)
    competitors = [
        Competitor(
            _pending_book(
                operation_key="race-1:1",
                call_id="race-1",
                patient_id="p0001",
                start=slot,
            )
        ),
        Competitor(
            _pending_book(
                operation_key="race-2:1",
                call_id="race-2",
                patient_id="p0002",
                start=slot,
            )
        ),
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)

    wins = [o for o in outcomes if o.kind == "ok"]
    losses = [o for o in outcomes if o.kind == "store_error"]
    assert len(wins) == 1
    assert len(losses) == 1
    assert losses[0].code == "slot_taken"

    conn = open_db(db_path)
    try:
        assert _count_booked_at(conn, "dent_1", slot, slot + timedelta(minutes=30)) == 1
        keys = {o.result.operation_key for o in wins if o.result is not None}
        ch_rows = conn.execute(
            "SELECT operation_key FROM changes WHERE operation_key IN ('race-1:1', 'race-2:1')"
        ).fetchall()
        assert len(ch_rows) == 1
        assert ch_rows[0]["operation_key"] in keys
    finally:
        conn.close()


def test_eight_callers_twenty_rounds(db_path: Path) -> None:
    patients = [f"p{i:04d}" for i in range(1, 9)]
    successes = 0
    slot_base = _madrid(2027, 1, 11, 9, 0)

    for rnd in range(20):
        slot = slot_base + timedelta(minutes=30 * rnd)
        competitors = [
            Competitor(
                _pending_book(
                    operation_key=f"r{rnd}-{i}:1",
                    call_id=f"r{rnd}-{i}",
                    patient_id=patients[i - 1],
                    start=slot,
                )
            )
            for i in range(1, 9)
        ]
        outcomes = _race(db_path, competitors)
        _assert_no_unexpected(outcomes)
        wins = [o for o in outcomes if o.kind == "ok"]
        losses = [o for o in outcomes if o.kind == "store_error"]
        assert len(wins) == 1
        assert len(losses) == 7
        assert all(o.code == "slot_taken" for o in losses)
        successes += 1

    conn = open_db(db_path)
    try:
        for rnd in range(20):
            slot = slot_base + timedelta(minutes=30 * rnd)
            assert _count_booked_at(conn, "dent_1", slot, slot + timedelta(minutes=30)) == 1
        assert successes == 20
    finally:
        conn.close()


def test_overlapping_slots_same_practitioner(db_path: Path) -> None:
    day = _madrid(2027, 1, 12, 9, 0)
    competitors = [
        Competitor(
            _pending_book(
                operation_key="ov-1:1",
                call_id="ov-1",
                patient_id="p0003",
                start=day,
                minutes=30,
            )
        ),
        Competitor(
            _pending_book(
                operation_key="ov-2:1",
                call_id="ov-2",
                patient_id="p0004",
                start=day + timedelta(minutes=15),
                minutes=30,
            )
        ),
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)
    wins = [o for o in outcomes if o.kind == "ok"]
    losses = [o for o in outcomes if o.kind == "store_error"]
    assert len(wins) == 1
    assert len(losses) == 1
    assert losses[0].code == "slot_taken"


def test_same_operation_key_four_threads(db_path: Path) -> None:
    pending = _pending_book(
        operation_key="idem:1",
        call_id="idem",
        patient_id="p0005",
        start=_madrid(2027, 1, 13, 10, 0),
    )
    competitors = [Competitor(pending) for _ in range(4)]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)

    results = [o.result for o in outcomes if o.kind == "ok" and o.result is not None]
    assert len(results) == 4
    fresh = [r for r in results if not r.already_saved]
    idem = [r for r in results if r.already_saved]
    assert len(fresh) == 1
    assert len(idem) == 3
    appt_ids = {r.appointment_id for r in results}
    change_sets = {tuple(r.change_ids) for r in results}
    assert len(appt_ids) == 1
    assert len(change_sets) == 1

    conn = open_db(db_path)
    try:
        appt_id = fresh[0].appointment_id
        assert appt_id is not None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE appointment_id = ? AND status = 'booked'",
                (appt_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM changes WHERE operation_key = 'idem:1'").fetchone()[
                0
            ]
            == 1
        )
    finally:
        conn.close()


def test_same_patient_two_practitioners(db_path: Path) -> None:
    slot = _madrid(2027, 1, 14, 11, 0)
    conn = open_db(db_path)
    treatment_b = _treatment_for(conn, "dent_2")
    conn.close()
    competitors = [
        Competitor(
            _pending_book(
                operation_key="ppo-1:1",
                call_id="ppo-1",
                patient_id="p0006",
                practitioner_id="dent_1",
                start=slot,
            )
        ),
        Competitor(
            _pending_book(
                operation_key="ppo-2:1",
                call_id="ppo-2",
                patient_id="p0006",
                practitioner_id="dent_2",
                treatment=treatment_b,
                start=slot,
            )
        ),
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)
    wins = [o for o in outcomes if o.kind == "ok"]
    losses = [o for o in outcomes if o.kind == "store_error"]
    assert len(wins) == 1
    assert len(losses) == 1
    assert losses[0].code == "patient_overlap"


def test_eight_non_overlapping_all_succeed(db_path: Path) -> None:
    slots: list[tuple[str, datetime]] = [
        ("dent_1", _madrid(2027, 1, 15, 9, 0)),
        ("dent_1", _madrid(2027, 1, 15, 10, 0)),
        ("dent_2", _madrid(2027, 1, 15, 9, 0)),
        ("dent_2", _madrid(2027, 1, 15, 10, 0)),
        ("dent_3", _madrid(2027, 1, 15, 9, 0)),
        ("dent_3", _madrid(2027, 1, 15, 10, 0)),
        ("dent_1", _madrid(2027, 1, 15, 11, 0)),
        ("dent_2", _madrid(2027, 1, 15, 11, 0)),
    ]
    conn = open_db(db_path)
    treatments = {pr: _treatment_for(conn, pr) for pr in {pr for pr, _ in slots}}
    conn.close()

    competitors = [
        Competitor(
            _pending_book(
                operation_key=f"free-{i}:1",
                call_id=f"free-{i}",
                patient_id=f"p{i:04d}",
                practitioner_id=pr,
                treatment=treatments[pr],
                start=start,
            )
        )
        for i, (pr, start) in enumerate(slots, start=1)
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)
    assert all(o.kind == "ok" for o in outcomes)
    assert all(o.result is not None and o.result.already_saved is False for o in outcomes)


def _pending_register_book(
    *,
    call_id: str,
    phone: str,
    patient_id: str,
    practitioner_id: str,
    treatment: str,
    start: datetime,
    minutes: int = 30,
) -> PendingChange:
    staging = Staging(call_id)
    staging.stage_register(
        NewPatient(
            first="Nuevo",
            last1="Concurrente",
            last2=None,
            dob="1992-03-03",
            phone=phone,
        ),
        patient_id,
    )
    return staging.stage_booking(
        Booking(
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            treatment=treatment,
            start=start,
            end=start + timedelta(minutes=minutes),
        )
    )


def test_register_book_races_plain_book(db_path: Path) -> None:
    slot = _madrid(2027, 1, 16, 10, 0)
    conn = open_db(db_path)
    treatment = _treatment_for(conn, "dent_1")
    conn.close()
    provisional = "p_race_new"
    competitors = [
        Competitor(
            _pending_register_book(
                call_id="race-reg",
                phone="+441111111101",
                patient_id=provisional,
                practitioner_id="dent_1",
                treatment=treatment,
                start=slot,
            )
        ),
        Competitor(
            _pending_book(
                operation_key="race-book:1",
                call_id="race-book",
                patient_id="p0007",
                start=slot,
            )
        ),
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)
    assert sum(1 for o in outcomes if o.kind == "ok") == 1
    assert sum(1 for o in outcomes if o.kind == "store_error") == 1

    winner = next(o for o in outcomes if o.kind == "ok")
    assert winner.result is not None
    conn = open_db(db_path)
    try:
        reg_changes = conn.execute(
            """
            SELECT COUNT(*) FROM changes
            WHERE operation_key IN ('race-reg:1', 'race-reg:1/register')
            """
        ).fetchone()[0]
        reg_patient = conn.execute(
            "SELECT COUNT(*) FROM patients WHERE created_by_call = 'race-reg'"
        ).fetchone()[0]
        if winner.result.kind == "book":
            assert reg_changes == 0
            assert reg_patient == 0
        else:
            assert winner.result.kind == "register_book"
            assert reg_changes == 2
            assert reg_patient == 1
        assert _count_booked_at(conn, "dent_1", slot, slot + timedelta(minutes=30)) == 1
    finally:
        conn.close()


def _move_to_slot(
    conn: sqlite3.Connection,
    appointment_id: str,
    *,
    operation_key: str,
    call_id: str,
    target: datetime,
) -> PendingChange:
    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
    ).fetchone()
    assert row is not None and row["status"] == "booked"
    seen_start = _utc_row(str(row["start_utc"]))
    seen_end = _utc_row(str(row["end_utc"]))
    minutes = int((seen_end - seen_start).total_seconds() // 60)
    end = target + timedelta(minutes=minutes)
    return PendingChange(
        operation_key=operation_key,
        call_id=call_id,
        kind="move",
        move=Move(
            appointment_id=appointment_id,
            patient_id=str(row["patient_id"]),
            seen_practitioner_id=str(row["practitioner_id"]),
            seen_start=seen_start,
            seen_end=seen_end,
            practitioner_id=str(row["practitioner_id"]),
            start=target,
            end=end,
        ),
    )


def test_move_races_book_into_target_slot(db_path: Path) -> None:
    appointment_id = "a00369"
    target = _madrid(2027, 1, 17, 10, 0)
    conn = open_db(db_path)
    before = conn.execute(
        "SELECT start_utc, end_utc, practitioner_id FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()
    move_pending = _move_to_slot(
        conn, appointment_id, operation_key="mv:1", call_id="mv", target=target
    )
    treatment = _treatment_for(conn, str(before["practitioner_id"]))
    conn.close()

    competitors = [
        Competitor(move_pending),
        Competitor(
            _pending_book(
                operation_key="bk:1",
                call_id="bk",
                patient_id="p0008",
                practitioner_id=str(before["practitioner_id"]),
                treatment=treatment,
                start=target,
            )
        ),
    ]
    outcomes = _race(db_path, competitors)
    _assert_no_unexpected(outcomes)
    assert sum(1 for o in outcomes if o.kind == "ok") == 1

    conn = open_db(db_path)
    try:
        row = conn.execute(
            "SELECT start_utc, end_utc FROM appointments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()
        move_won = any(
            o.kind == "ok" and o.result is not None and o.result.kind == "move" for o in outcomes
        )
        if not move_won:
            assert row["start_utc"] == before["start_utc"]
            assert row["end_utc"] == before["end_utc"]
    finally:
        conn.close()


def test_cancel_races_move_same_appointment(db_path: Path) -> None:
    appointment_id = "a00372"
    target = _madrid(2027, 1, 18, 9, 30)
    conn = open_db(db_path)
    row = conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
    ).fetchone()
    assert row is not None
    seen_start = _utc_row(str(row["start_utc"]))
    seen_end = _utc_row(str(row["end_utc"]))
    original_start = str(row["start_utc"])
    original_end = str(row["end_utc"])
    move_pending = _move_to_slot(
        conn, appointment_id, operation_key="cm-m:1", call_id="cm-m", target=target
    )
    cancel_pending = PendingChange(
        operation_key="cm-c:1",
        call_id="cm-c",
        kind="cancel",
        cancel=Cancel(
            appointment_id=appointment_id,
            patient_id=str(row["patient_id"]),
            seen_practitioner_id=str(row["practitioner_id"]),
            seen_start=seen_start,
            seen_end=seen_end,
        ),
    )
    conn.close()

    outcomes = _race(db_path, [Competitor(move_pending), Competitor(cancel_pending)])
    _assert_no_unexpected(outcomes)
    wins = [o for o in outcomes if o.kind == "ok"]
    losses = [o for o in outcomes if o.kind == "store_error"]
    assert len(wins) == 1
    assert len(losses) == 1
    assert losses[0].code == "appointment_not_seen"

    conn = open_db(db_path)
    try:
        after = conn.execute(
            "SELECT status, start_utc, end_utc FROM appointments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()
        cancelled = after["status"] == "cancelled"
        moved = after["status"] == "booked" and after["start_utc"] == utc_text(target)
        assert cancelled or moved
        assert not (cancelled and moved)
        if cancelled:
            assert after["start_utc"] == original_start
            assert after["end_utc"] == original_end
        if moved:
            assert after["start_utc"] != original_start
    finally:
        conn.close()


def test_busy_writer_short_timeout_then_default(db_path: Path) -> None:
    slot = _madrid(2027, 1, 19, 10, 0)
    pending = _pending_book(
        operation_key="busy:1",
        call_id="busy",
        patient_id="p0009",
        start=slot,
    )

    def hold_lock(db: Path, barrier: threading.Barrier) -> None:
        conn = open_db(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            barrier.wait(timeout=JOIN_TIMEOUT_S)
            time.sleep(HOLD_LOCK_S)
            conn.execute("ROLLBACK")
        finally:
            conn.close()

    def try_save(
        db: Path, barrier: threading.Barrier, timeout_ms: int, out: list[ThreadOutcome]
    ) -> None:
        conn = open_db(db, busy_timeout_ms=timeout_ms)
        try:
            barrier.wait(timeout=JOIN_TIMEOUT_S)
            try:
                result = save(conn, pending, now=NOW, saved_utc=SAVED_UTC)
                out.append(ThreadOutcome(kind="ok", result=result))
            except StoreError as exc:
                out.append(ThreadOutcome(kind="store_error", code=exc.code))
        finally:
            conn.close()

    # Short timeout while lock is held → store_unavailable, nothing saved.
    barrier1 = threading.Barrier(2)
    short_out: list[ThreadOutcome] = []
    holder1 = threading.Thread(target=hold_lock, args=(db_path, barrier1))
    saver1 = threading.Thread(target=try_save, args=(db_path, barrier1, 100, short_out))
    holder1.start()
    saver1.start()
    holder1.join(timeout=JOIN_TIMEOUT_S)
    saver1.join(timeout=JOIN_TIMEOUT_S)
    assert not holder1.is_alive() and not saver1.is_alive()
    assert len(short_out) == 1
    assert short_out[0].kind == "store_error"
    assert short_out[0].code == "store_unavailable"

    conn = open_db(db_path)
    before = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    conn.close()

    # Default timeout waits out the lock and succeeds.
    pending2 = _pending_book(
        operation_key="busy:2",
        call_id="busy",
        patient_id="p0010",
        start=_madrid(2027, 1, 19, 11, 0),
    )

    def try_save2(db: Path, barrier: threading.Barrier, out: list[ThreadOutcome]) -> None:
        conn = open_db(db)
        try:
            barrier.wait(timeout=JOIN_TIMEOUT_S)
            try:
                result = save(conn, pending2, now=NOW, saved_utc=SAVED_UTC)
                out.append(ThreadOutcome(kind="ok", result=result))
            except StoreError as exc:
                out.append(ThreadOutcome(kind="store_error", code=exc.code))
        finally:
            conn.close()

    barrier2 = threading.Barrier(2)
    long_out: list[ThreadOutcome] = []
    holder2 = threading.Thread(target=hold_lock, args=(db_path, barrier2))
    saver2 = threading.Thread(target=try_save2, args=(db_path, barrier2, long_out))
    holder2.start()
    saver2.start()
    holder2.join(timeout=JOIN_TIMEOUT_S)
    saver2.join(timeout=JOIN_TIMEOUT_S)
    assert not holder2.is_alive() and not saver2.is_alive()
    assert len(long_out) == 1
    assert long_out[0].kind == "ok"
    assert long_out[0].result is not None
    assert long_out[0].result.appointment_id is not None

    conn = open_db(db_path)
    after = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    conn.close()
    assert after == before + 1
