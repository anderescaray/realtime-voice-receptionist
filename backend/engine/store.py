"""Clinic store: database connection helpers and per-call staging."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
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


def _same_day_urgent(booking: Booking, now: datetime) -> bool:
    if booking.treatment != "urgency":
        return False
    start_local = booking.start.astimezone(MADRID).date()
    now_local = now.astimezone(MADRID).date()
    return start_local == now_local


def _next_patient_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT patient_id FROM patients").fetchall()
    max_n = 0
    for row in rows:
        match = _PATIENT_ID_TAIL.search(str(row["patient_id"]))
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"p_{max_n + 1:04d}"


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


def _book_details(
    appointment_id: str,
    booking: Booking,
    *,
    same_day_urgent: bool,
) -> str:
    payload = {
        "before": None,
        "after": {
            "appointment": {
                "appointment_id": appointment_id,
                "patient_id": booking.patient_id,
                "practitioner_id": booking.practitioner_id,
                "treatment_id": booking.treatment,
                "start_utc": utc_text(booking.start),
                "end_utc": utc_text(booking.end),
                "status": "booked",
                "same_day_urgent": 1 if same_day_urgent else 0,
            }
        },
    }
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


def _overlap_code(conn: sqlite3.Connection, booking: Booking) -> str:
    start_utc = utc_text(booking.start)
    end_utc = utc_text(booking.end)
    row = conn.execute(
        """
        SELECT 1 FROM appointments
        WHERE status = 'booked'
          AND practitioner_id = ?
          AND start_utc < ?
          AND end_utc > ?
        LIMIT 1
        """,
        (booking.practitioner_id, end_utc, start_utc),
    ).fetchone()
    if row is not None:
        return "slot_taken"
    return "patient_overlap"


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
        patient_id: str | None = None
        if reg_row is not None:
            reg_details = _parse_change_row(reg_row)
            patient_id = str(reg_details["after"]["patient"]["patient_id"])
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
    return _result_from_stored(conn, change, already_saved=True)


def _validate_change(change: PendingChange) -> None:
    kind = change.kind
    if kind in ("move", "cancel"):
        raise StoreError("bad_change", "not implemented yet")
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
    """Persist a staged register, book, or register_book change."""
    _require_aware(now, "now")
    _require_aware(saved_utc, "saved_utc")
    _validate_change(change)

    _begin(conn)

    patient_id: str | None = None
    appointment_id: str | None = None
    change_ids: list[int] = []
    same_day_urgent = False

    try:
        existing = _existing_result(conn, change)
        if existing is not None:
            conn.execute("COMMIT")
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
            same_day_urgent = _same_day_urgent(booking, now)
            try:
                appointment_id = _insert_appointment(
                    conn,
                    booking=booking,
                    call_id=change.call_id,
                    same_day_urgent=same_day_urgent,
                )
            except sqlite3.IntegrityError as exc:
                if "overlap" in str(exc):
                    code = _overlap_code(conn, booking)
                    _rollback(conn)
                    raise StoreError(code) from exc
                _rollback(conn)
                raise StoreError("bad_change") from exc

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

        conn.execute("COMMIT")
        return SaveResult(
            operation_key=change.operation_key,
            kind=change.kind,
            patient_id=patient_id,
            appointment_id=appointment_id,
            change_ids=change_ids,
            same_day_urgent=same_day_urgent,
            already_saved=False,
        )
    except StoreError:
        _rollback(conn)
        raise
    except sqlite3.OperationalError as exc:
        _rollback(conn)
        if _is_store_unavailable(exc):
            raise StoreError("store_unavailable") from exc
        raise
    except sqlite3.IntegrityError as exc:
        _rollback(conn)
        raise StoreError("bad_change") from exc
    except BaseException:
        # Never leave a write transaction open on an unexpected error.
        _rollback(conn)
        raise
