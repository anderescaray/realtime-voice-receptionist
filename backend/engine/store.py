"""Clinic store: database connection helpers and per-call staging."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

ERROR_CODES = frozenset(
    {
        "slot_taken",
        "patient_overlap",
        "already_registered",
        "appointment_not_seen",
        "store_unavailable",
        "pending_exists",
        "nothing_pending",
        "bad_change",
    }
)

_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PATIENT_ID_TAIL = re.compile(r"(\d+)$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SQLITE_UNAVAILABLE = frozenset({5, 6, 8, 10, 13, 14})


class StoreError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"invalid store error code: {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def open_db(path: str | Path, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open SQLite with WAL, foreign keys, and caller-controlled busy timeout."""
    conn = sqlite3.connect(
        path,
        isolation_level=None,
        timeout=busy_timeout_ms / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def utc_text(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise StoreError("bad_change", "datetime must be timezone-aware")
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_span(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise StoreError("bad_change", "start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise StoreError("bad_change", "end must be timezone-aware")
    if end <= start:
        raise StoreError("bad_change", "end must be after start")


def _validate_dob(dob: str) -> None:
    if not _DOB_RE.fullmatch(dob):
        raise StoreError("bad_change", "dob must be YYYY-MM-DD")
    year, month, day = (int(part) for part in dob.split("-"))
    try:
        date(year, month, day)
    except ValueError as exc:
        raise StoreError("bad_change", "dob is not a valid date") from exc


@dataclass(frozen=True)
class NewPatient:
    first: str
    last1: str
    last2: str | None
    dob: str
    phone: str
    language: str = "es"

    def __post_init__(self) -> None:
        if not self.first.strip():
            raise StoreError("bad_change", "first name required")
        if not self.last1.strip():
            raise StoreError("bad_change", "last1 required")
        if not self.phone.strip():
            raise StoreError("bad_change", "phone required")
        _validate_dob(self.dob)


@dataclass(frozen=True)
class Booking:
    patient_id: str
    practitioner_id: str
    treatment: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _validate_span(self.start, self.end)


@dataclass(frozen=True)
class Move:
    appointment_id: str
    patient_id: str
    seen_practitioner_id: str
    seen_start: datetime
    seen_end: datetime
    practitioner_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _validate_span(self.seen_start, self.seen_end)
        _validate_span(self.start, self.end)


@dataclass(frozen=True)
class Cancel:
    appointment_id: str
    patient_id: str
    seen_practitioner_id: str
    seen_start: datetime
    seen_end: datetime

    def __post_init__(self) -> None:
        _validate_span(self.seen_start, self.seen_end)


@dataclass(frozen=True)
class PendingChange:
    operation_key: str
    call_id: str
    kind: str
    new_patient: NewPatient | None = None
    provisional_patient_id: str | None = None
    booking: Booking | None = None
    move: Move | None = None
    cancel: Cancel | None = None


class Staging:
    """In-memory pending change for one call; no database access."""

    def __init__(self, call_id: str) -> None:
        self._call_id = call_id
        self._pending: PendingChange | None = None
        self._next_n = 1

    @property
    def pending(self) -> PendingChange | None:
        return self._pending

    def _mint_key(self) -> str:
        key = f"{self._call_id}:{self._next_n}"
        self._next_n += 1
        return key

    def stage_register(self, patient: NewPatient, provisional_patient_id: str) -> PendingChange:
        if self._pending is not None:
            raise StoreError("pending_exists")
        pending = PendingChange(
            operation_key=self._mint_key(),
            call_id=self._call_id,
            kind="register",
            new_patient=patient,
            provisional_patient_id=provisional_patient_id,
        )
        self._pending = pending
        return pending

    def stage_booking(self, booking: Booking) -> PendingChange:
        current = self._pending
        if current is not None:
            if current.kind == "register" and current.provisional_patient_id == booking.patient_id:
                pending = PendingChange(
                    operation_key=current.operation_key,
                    call_id=self._call_id,
                    kind="register_book",
                    new_patient=current.new_patient,
                    provisional_patient_id=current.provisional_patient_id,
                    booking=booking,
                )
                self._pending = pending
                return pending
            raise StoreError("pending_exists")
        pending = PendingChange(
            operation_key=self._mint_key(),
            call_id=self._call_id,
            kind="book",
            booking=booking,
        )
        self._pending = pending
        return pending

    def stage_move(self, move: Move) -> PendingChange:
        if self._pending is not None:
            raise StoreError("pending_exists")
        pending = PendingChange(
            operation_key=self._mint_key(),
            call_id=self._call_id,
            kind="move",
            move=move,
        )
        self._pending = pending
        return pending

    def stage_cancel(self, cancel: Cancel) -> PendingChange:
        if self._pending is not None:
            raise StoreError("pending_exists")
        pending = PendingChange(
            operation_key=self._mint_key(),
            call_id=self._call_id,
            kind="cancel",
            cancel=cancel,
        )
        self._pending = pending
        return pending

    def discard(self) -> PendingChange:
        if self._pending is None:
            raise StoreError("nothing_pending")
        dropped = self._pending
        self._pending = None
        return dropped

    def clear(self) -> PendingChange:
        return self.discard()


@dataclass(frozen=True)
class SaveResult:
    operation_key: str
    kind: str
    patient_id: str | None
    appointment_id: str | None
    change_ids: list[int]
    same_day_urgent: bool
    already_saved: bool


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise StoreError("bad_change", f"{label} must be timezone-aware")


def _is_store_unavailable(exc: sqlite3.OperationalError) -> bool:
    return (exc.sqlite_errorcode & 0xFF) in _SQLITE_UNAVAILABLE


def _begin(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_store_unavailable(exc):
            raise StoreError("store_unavailable") from exc
        raise


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


@contextmanager
def _write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN IMMEDIATE ... COMMIT; any failure rolls back, so the database is unchanged.

    A database that cannot be written (busy past the timeout, locked, read-only, I/O,
    full) becomes `store_unavailable`; a constraint failure the caller did not handle
    becomes `bad_change`. Any other error is a bug and is re-raised as it is.
    """
    _begin(conn)
    try:
        yield
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        _rollback(conn)
        if _is_store_unavailable(exc):
            raise StoreError("store_unavailable") from exc
        raise
    except sqlite3.IntegrityError as exc:
        _rollback(conn)
        raise StoreError("bad_change") from exc
    except BaseException:
        _rollback(conn)
        raise


def _same_day_urgent(treatment_id: str, start: datetime, now: datetime) -> bool:
    """An urgency appointment on the call's own day (Madrid dates) is flagged for staff."""
    if treatment_id != "urgency":
        return False
    start_local = start.astimezone(MADRID).date()
    now_local = now.astimezone(MADRID).date()
    return start_local == now_local


_NEXT_PATIENT_KEY = "next_patient_n"


def _next_patient_id(conn: sqlite3.Connection) -> str:
    """A patient number that never comes back, even after a purge deleted the newest patient.

    The counter lives in `meta` and moves only forward, inside the caller's write
    transaction; it also starts above every existing id, whatever its prefix.
    """
    max_n = 0
    for row in conn.execute("SELECT patient_id FROM patients"):
        match = _PATIENT_ID_TAIL.search(str(row["patient_id"]))
        if match:
            max_n = max(max_n, int(match.group(1)))
    stored = conn.execute("SELECT value FROM meta WHERE key = ?", (_NEXT_PATIENT_KEY,)).fetchone()
    n = max(max_n + 1, int(stored["value"]) if stored is not None else 0)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_NEXT_PATIENT_KEY, str(n + 1)),
    )
    return f"p_{n:04d}"


def _patient_exists(conn: sqlite3.Connection, patient: NewPatient) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM patients
        WHERE first_name = ?
          AND last_name1 = ?
          AND dob = ?
          AND (
            (last_name2 IS NULL AND ? IS NULL)
            OR last_name2 = ?
          )
        LIMIT 1
        """,
        (patient.first, patient.last1, patient.dob, patient.last2, patient.last2),
    ).fetchone()
    return row is not None


def _register_details(patient_id: str, patient: NewPatient) -> str:
    payload = {
        "before": None,
        "after": {
            "patient": {
                "patient_id": patient_id,
                "first_name": patient.first,
                "last_name1": patient.last1,
                "last_name2": patient.last2,
                "dob": patient.dob,
                "phone": patient.phone,
                "language": patient.language,
            }
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _appointment_dict(
    *,
    appointment_id: str,
    patient_id: str,
    practitioner_id: str,
    treatment_id: str,
    start: datetime,
    end: datetime,
    status: str,
    same_day_urgent: bool,
) -> dict[str, Any]:
    return {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "practitioner_id": practitioner_id,
        "treatment_id": treatment_id,
        "start_utc": utc_text(start),
        "end_utc": utc_text(end),
        "status": status,
        "same_day_urgent": 1 if same_day_urgent else 0,
    }


def _book_details(
    appointment_id: str,
    booking: Booking,
    *,
    same_day_urgent: bool,
) -> str:
    payload = {
        "before": None,
        "after": {
            "appointment": _appointment_dict(
                appointment_id=appointment_id,
                patient_id=booking.patient_id,
                practitioner_id=booking.practitioner_id,
                treatment_id=booking.treatment,
                start=booking.start,
                end=booking.end,
                status="booked",
                same_day_urgent=same_day_urgent,
            )
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _appointment_dict_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "appointment_id": str(row["appointment_id"]),
        "patient_id": str(row["patient_id"]),
        "practitioner_id": str(row["practitioner_id"]),
        "treatment_id": str(row["treatment_id"]),
        "start_utc": str(row["start_utc"]),
        "end_utc": str(row["end_utc"]),
        "status": str(row["status"]),
        "same_day_urgent": int(row["same_day_urgent"]),
    }


def _move_cancel_details(*, before: dict[str, Any], after: dict[str, Any]) -> str:
    payload = {"before": {"appointment": before}, "after": {"appointment": after}}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _insert_change(
    conn: sqlite3.Connection,
    *,
    operation_key: str,
    call_id: str,
    action: str,
    details: str,
    saved_utc: datetime,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO changes (operation_key, call_id, action, details, saved_utc)
        VALUES (?, ?, ?, ?, ?)
        """,
        (operation_key, call_id, action, details, utc_text(saved_utc)),
    )
    return int(cursor.lastrowid)


def _insert_patient(
    conn: sqlite3.Connection,
    *,
    patient: NewPatient,
    call_id: str,
) -> str:
    if _patient_exists(conn, patient):
        raise StoreError("already_registered")
    patient_id = _next_patient_id(conn)
    conn.execute(
        """
        INSERT INTO patients (
            patient_id, first_name, last_name1, last_name2, dob,
            phone, language, created_by_call
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            patient.first,
            patient.last1,
            patient.last2,
            patient.dob,
            patient.phone,
            patient.language,
            call_id,
        ),
    )
    return patient_id


def _mint_appointment_id(conn: sqlite3.Connection) -> str:
    for _ in range(5):
        appointment_id = f"a_{secrets.token_hex(4)}"
        row = conn.execute(
            "SELECT 1 FROM appointments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()
        if row is None:
            return appointment_id
    raise StoreError("store_unavailable", "could not mint appointment id")


def _insert_appointment(
    conn: sqlite3.Connection,
    *,
    booking: Booking,
    call_id: str,
    same_day_urgent: bool,
) -> str:
    appointment_id = _mint_appointment_id(conn)
    conn.execute(
        """
        INSERT INTO appointments (
            appointment_id, patient_id, practitioner_id, treatment_id,
            start_utc, end_utc, status, same_day_urgent, created_by_call
        ) VALUES (?, ?, ?, ?, ?, ?, 'booked', ?, ?)
        """,
        (
            appointment_id,
            booking.patient_id,
            booking.practitioner_id,
            booking.treatment,
            utc_text(booking.start),
            utc_text(booking.end),
            1 if same_day_urgent else 0,
            call_id,
        ),
    )
    return appointment_id


def _overlap_code(
    conn: sqlite3.Connection,
    *,
    practitioner_id: str,
    start: datetime,
    end: datetime,
    exclude_appointment_id: str | None = None,
) -> str:
    """Which diary the overlap trigger refused for: the practitioner's wins over the patient's."""
    row = conn.execute(
        """
        SELECT 1 FROM appointments
        WHERE status = 'booked'
          AND appointment_id IS NOT ?
          AND practitioner_id = ?
          AND start_utc < ?
          AND end_utc > ?
        LIMIT 1
        """,
        (exclude_appointment_id, practitioner_id, utc_text(end), utc_text(start)),
    ).fetchone()
    return "slot_taken" if row is not None else "patient_overlap"


def _fetch_booked_appointment(conn: sqlite3.Connection, appointment_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM appointments WHERE appointment_id = ?",
        (appointment_id,),
    ).fetchone()


def _assert_appointment_seen(
    row: sqlite3.Row | None,
    *,
    patient_id: str,
    seen_practitioner_id: str,
    seen_start: datetime,
    seen_end: datetime,
) -> None:
    if row is None:
        raise StoreError("appointment_not_seen")
    if str(row["status"]) != "booked":
        raise StoreError("appointment_not_seen")
    if str(row["patient_id"]) != patient_id:
        raise StoreError("appointment_not_seen")
    if str(row["practitioner_id"]) != seen_practitioner_id:
        raise StoreError("appointment_not_seen")
    if str(row["start_utc"]) != utc_text(seen_start):
        raise StoreError("appointment_not_seen")
    if str(row["end_utc"]) != utc_text(seen_end):
        raise StoreError("appointment_not_seen")


def _parse_change_row(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(str(row["details"]))


def _result_from_stored(
    conn: sqlite3.Connection,
    change: PendingChange,
    *,
    already_saved: bool,
) -> SaveResult:
    book_row = conn.execute(
        "SELECT * FROM changes WHERE operation_key = ?",
        (change.operation_key,),
    ).fetchone()
    if book_row is not None and str(book_row["action"]) == "book":
        reg_row = conn.execute(
            "SELECT * FROM changes WHERE operation_key = ?",
            (f"{change.operation_key}/register",),
        ).fetchone()
        kind = "register_book" if reg_row is not None else "book"
        book_details = _parse_change_row(book_row)
        appt = book_details["after"]["appointment"]
        change_ids: list[int] = []
        if reg_row is not None:
            change_ids.append(int(reg_row["change_id"]))
        change_ids.append(int(book_row["change_id"]))
        return SaveResult(
            operation_key=change.operation_key,
            kind=kind,
            patient_id=str(appt["patient_id"]),
            appointment_id=str(appt["appointment_id"]),
            change_ids=change_ids,
            same_day_urgent=bool(appt["same_day_urgent"]),
            already_saved=already_saved,
        )

    for action, kind in (("move", "move"), ("cancel", "cancel")):
        row = conn.execute(
            "SELECT * FROM changes WHERE operation_key = ? AND action = ?",
            (change.operation_key, action),
        ).fetchone()
        if row is not None:
            details = _parse_change_row(row)
            appt = details["after"]["appointment"]
            return SaveResult(
                operation_key=change.operation_key,
                kind=kind,
                patient_id=str(appt["patient_id"]),
                appointment_id=str(appt["appointment_id"]),
                change_ids=[int(row["change_id"])],
                # As on the first save: a cancel never raises the staff flag.
                same_day_urgent=kind == "move" and bool(appt["same_day_urgent"]),
                already_saved=already_saved,
            )

    reg_row = conn.execute(
        "SELECT * FROM changes WHERE operation_key = ? AND action = 'register'",
        (change.operation_key,),
    ).fetchone()
    if reg_row is None:
        raise StoreError("bad_change", "stored change missing")
    reg_details = _parse_change_row(reg_row)
    patient_id = str(reg_details["after"]["patient"]["patient_id"])
    return SaveResult(
        operation_key=change.operation_key,
        kind="register",
        patient_id=patient_id,
        appointment_id=None,
        change_ids=[int(reg_row["change_id"])],
        same_day_urgent=False,
        already_saved=already_saved,
    )


def _existing_result(conn: sqlite3.Connection, change: PendingChange) -> SaveResult | None:
    row = conn.execute(
        "SELECT 1 FROM changes WHERE operation_key = ?",
        (change.operation_key,),
    ).fetchone()
    if row is None:
        return None
    result = _result_from_stored(conn, change, already_saved=True)
    if result.kind != change.kind:
        raise StoreError("bad_change", "operation key already saved for another kind of change")
    return result


def _validate_change(change: PendingChange) -> None:
    kind = change.kind
    if kind == "move":
        if change.move is None:
            raise StoreError("bad_change", "move requires move")
        return
    if kind == "cancel":
        if change.cancel is None:
            raise StoreError("bad_change", "cancel requires cancel")
        return
    if kind == "register":
        if change.new_patient is None:
            raise StoreError("bad_change", "register requires new_patient")
        return
    if kind == "book":
        if change.booking is None:
            raise StoreError("bad_change", "book requires booking")
        return
    if kind == "register_book":
        if change.new_patient is None or change.booking is None:
            raise StoreError("bad_change", "register_book requires patient and booking")
        return
    raise StoreError("bad_change", f"unknown kind {kind!r}")


def save(
    conn: sqlite3.Connection,
    change: PendingChange,
    *,
    now: datetime,
    saved_utc: datetime,
) -> SaveResult:
    """Persist a staged change (register, book, register_book, move, or cancel)."""
    _require_aware(now, "now")
    _require_aware(saved_utc, "saved_utc")
    _validate_change(change)

    patient_id: str | None = None
    appointment_id: str | None = None
    change_ids: list[int] = []
    same_day_urgent = False

    with _write_transaction(conn):
        existing = _existing_result(conn, change)
        if existing is not None:
            return existing

        if change.kind in ("register", "register_book"):
            assert change.new_patient is not None
            patient_id = _insert_patient(conn, patient=change.new_patient, call_id=change.call_id)
            reg_key = (
                change.operation_key
                if change.kind == "register"
                else f"{change.operation_key}/register"
            )
            reg_details = _register_details(patient_id, change.new_patient)
            change_ids.append(
                _insert_change(
                    conn,
                    operation_key=reg_key,
                    call_id=change.call_id,
                    action="register",
                    details=reg_details,
                    saved_utc=saved_utc,
                )
            )

        if change.kind in ("book", "register_book"):
            assert change.booking is not None
            booking = change.booking
            if change.kind == "book":
                patient_id = booking.patient_id
            if change.kind == "register_book":
                assert patient_id is not None
                booking = Booking(
                    patient_id=patient_id,
                    practitioner_id=booking.practitioner_id,
                    treatment=booking.treatment,
                    start=booking.start,
                    end=booking.end,
                )
            same_day_urgent = _same_day_urgent(booking.treatment, booking.start, now)
            try:
                appointment_id = _insert_appointment(
                    conn,
                    booking=booking,
                    call_id=change.call_id,
                    same_day_urgent=same_day_urgent,
                )
            except sqlite3.IntegrityError as exc:
                if "overlap" not in str(exc):
                    raise
                code = _overlap_code(
                    conn,
                    practitioner_id=booking.practitioner_id,
                    start=booking.start,
                    end=booking.end,
                )
                raise StoreError(code) from exc

            book_details = _book_details(
                appointment_id,
                booking,
                same_day_urgent=same_day_urgent,
            )
            change_ids.append(
                _insert_change(
                    conn,
                    operation_key=change.operation_key,
                    call_id=change.call_id,
                    action="book",
                    details=book_details,
                    saved_utc=saved_utc,
                )
            )

        if change.kind == "move":
            assert change.move is not None
            move = change.move
            row = _fetch_booked_appointment(conn, move.appointment_id)
            _assert_appointment_seen(
                row,
                patient_id=move.patient_id,
                seen_practitioner_id=move.seen_practitioner_id,
                seen_start=move.seen_start,
                seen_end=move.seen_end,
            )
            assert row is not None
            before = _appointment_dict_from_row(row)
            treatment_id = str(row["treatment_id"])
            same_day_urgent = _same_day_urgent(treatment_id, move.start, now)
            try:
                conn.execute(
                    """
                    UPDATE appointments
                    SET practitioner_id = ?, start_utc = ?, end_utc = ?, same_day_urgent = ?
                    WHERE appointment_id = ?
                    """,
                    (
                        move.practitioner_id,
                        utc_text(move.start),
                        utc_text(move.end),
                        1 if same_day_urgent else 0,
                        move.appointment_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "overlap" not in str(exc):
                    raise
                code = _overlap_code(
                    conn,
                    practitioner_id=move.practitioner_id,
                    start=move.start,
                    end=move.end,
                    exclude_appointment_id=move.appointment_id,
                )
                raise StoreError(code) from exc
            after_row = _fetch_booked_appointment(conn, move.appointment_id)
            assert after_row is not None
            after = _appointment_dict_from_row(after_row)
            patient_id = move.patient_id
            appointment_id = move.appointment_id
            change_ids.append(
                _insert_change(
                    conn,
                    operation_key=change.operation_key,
                    call_id=change.call_id,
                    action="move",
                    details=_move_cancel_details(before=before, after=after),
                    saved_utc=saved_utc,
                )
            )

        if change.kind == "cancel":
            assert change.cancel is not None
            cancel = change.cancel
            row = _fetch_booked_appointment(conn, cancel.appointment_id)
            _assert_appointment_seen(
                row,
                patient_id=cancel.patient_id,
                seen_practitioner_id=cancel.seen_practitioner_id,
                seen_start=cancel.seen_start,
                seen_end=cancel.seen_end,
            )
            assert row is not None
            before = _appointment_dict_from_row(row)
            conn.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE appointment_id = ?",
                (cancel.appointment_id,),
            )
            after_row = conn.execute(
                "SELECT * FROM appointments WHERE appointment_id = ?",
                (cancel.appointment_id,),
            ).fetchone()
            assert after_row is not None
            after = _appointment_dict_from_row(after_row)
            patient_id = cancel.patient_id
            appointment_id = cancel.appointment_id
            # A cancel never raises the staff flag, whatever the row carried.
            same_day_urgent = False
            change_ids.append(
                _insert_change(
                    conn,
                    operation_key=change.operation_key,
                    call_id=change.call_id,
                    action="cancel",
                    details=_move_cancel_details(before=before, after=after),
                    saved_utc=saved_utc,
                )
            )

        return SaveResult(
            operation_key=change.operation_key,
            kind=change.kind,
            patient_id=patient_id,
            appointment_id=appointment_id,
            change_ids=change_ids,
            same_day_urgent=same_day_urgent,
            already_saved=False,
        )


def _mint_message_id(conn: sqlite3.Connection) -> str:
    for _ in range(5):
        message_id = f"m_{secrets.token_hex(4)}"
        row = conn.execute(
            "SELECT 1 FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return message_id
    raise StoreError("store_unavailable", "could not mint message id")


def add_message(
    conn: sqlite3.Connection,
    *,
    call_id: str,
    name: str,
    phone: str,
    text: str,
    urgent: bool,
    saved_utc: datetime,
) -> str:
    if not call_id.strip():
        raise StoreError("bad_change", "call_id required")
    if not name.strip():
        raise StoreError("bad_change", "name required")
    if not phone.strip():
        raise StoreError("bad_change", "phone required")
    if not text.strip():
        raise StoreError("bad_change", "text required")
    _require_aware(saved_utc, "saved_utc")
    with _write_transaction(conn):
        message_id = _mint_message_id(conn)
        conn.execute(
            """
            INSERT INTO messages (message_id, call_id, name, phone, text, urgent, saved_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                call_id,
                name,
                phone,
                text,
                1 if urgent else 0,
                utc_text(saved_utc),
            ),
        )
        return message_id


def add_review_link(
    conn: sqlite3.Connection,
    *,
    call_id: str,
    token_sha256: str,
    expires_utc: datetime,
) -> None:
    if not _SHA256_HEX_RE.fullmatch(token_sha256):
        raise StoreError("bad_change", "token_sha256 must be 64 lowercase hex characters")
    _require_aware(expires_utc, "expires_utc")
    with _write_transaction(conn):
        conn.execute(
            """
            INSERT INTO review_links (token_sha256, call_id, expires_utc)
            VALUES (?, ?, ?)
            """,
            (token_sha256, call_id, utc_text(expires_utc)),
        )


def expired_calls(conn: sqlite3.Connection, *, now: datetime) -> list[str]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise StoreError("bad_change", "now must be timezone-aware")
    now_text = utc_text(now)
    rows = conn.execute(
        """
        SELECT call_id FROM review_links
        WHERE expires_utc <= ?
        ORDER BY call_id
        """,
        (now_text,),
    ).fetchall()
    return [str(row["call_id"]) for row in rows]


def purge_call(conn: sqlite3.Connection, call_id: str, *, now: datetime) -> dict[str, int]:
    """Delete a public call's rows once its review link has expired (contracts §11)."""
    _require_aware(now, "now")
    with _write_transaction(conn):
        link = conn.execute(
            "SELECT expires_utc FROM review_links WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        if link is None:
            raise StoreError("bad_change", "no review link")
        if str(link["expires_utc"]) > utc_text(now):
            raise StoreError("bad_change", "review link not expired yet")
        cur = conn.execute(
            """
            DELETE FROM appointments
            WHERE patient_id IN (SELECT patient_id FROM patients WHERE created_by_call = ?)
               OR created_by_call = ?
            """,
            (call_id, call_id),
        )
        appointments = cur.rowcount
        cur = conn.execute("DELETE FROM patients WHERE created_by_call = ?", (call_id,))
        patients = cur.rowcount
        cur = conn.execute("DELETE FROM messages WHERE call_id = ?", (call_id,))
        messages = cur.rowcount
        cur = conn.execute("DELETE FROM changes WHERE call_id = ?", (call_id,))
        changes = cur.rowcount
        cur = conn.execute("DELETE FROM review_links WHERE call_id = ?", (call_id,))
        review_links = cur.rowcount
        return {
            "appointments": appointments,
            "patients": patients,
            "messages": messages,
            "changes": changes,
            "review_links": review_links,
        }
