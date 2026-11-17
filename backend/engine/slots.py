"""Free slots on the clinic diary, with a blocking reason for every practitioner without one.

The caller passes the clock (`now`) and the settings; nothing here reads the wall clock
or the environment. Times are stored as UTC text and handled as Europe/Madrid here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

DEFAULT_BOOKING_HORIZON_DAYS = 28
DEFAULT_URGENCY_MIN_LEAD_MINUTES = 120
GRID_MINUTES = 15
AFTERNOON_STARTS = "14:00"

URGENCY = "urgency"
PARTS_OF_DAY = ("morning", "afternoon")

#  Blocking reasons, contracts §5.6.
NOT_WORKING_THAT_DAY = "not_working_that_day"
ABSENT = "absent"
CLINIC_CLOSED = "clinic_closed"
FULLY_BOOKED = "fully_booked"
OUTSIDE_PART_OF_DAY = "outside_part_of_day"
PATIENT_BUSY = "patient_busy"
BEYOND_HORIZON = "beyond_horizon"
WITHIN_LEAD_TIME = "within_lead_time"

_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
_MINUTES_PER_DAY = 24 * 60


class SlotSearchError(Exception):
    """Bad input to a search: the `.code` is a refusal code from contracts §5.4."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Slot:
    practitioner_id: str
    treatment: str
    start: datetime
    end: datetime
    same_day: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "practitioner_id": self.practitioner_id,
            "treatment": self.treatment,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "same_day": self.same_day,
        }


@dataclass(frozen=True)
class BlockReason:
    reason: str
    dates: list[str]
    until: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"reason": self.reason}
        if self.dates:
            out["dates"] = list(self.dates)
        if self.until is not None:
            out["until"] = self.until
        return out


@dataclass(frozen=True)
class Blocked:
    practitioner_id: str
    reasons: list[BlockReason]

    def as_dict(self) -> dict[str, Any]:
        return {
            "practitioner_id": self.practitioner_id,
            "reasons": [r.as_dict() for r in self.reasons],
        }


@dataclass(frozen=True)
class SlotSearch:
    slots: list[Slot]
    blocked: list[Blocked]
    window_start: date
    window_end: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "slots": [s.as_dict() for s in self.slots],
            "blocked": [b.as_dict() for b in self.blocked],
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }


def search_slots(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    treatment: str,
    practitioner_id: str | None = None,
    after: date | None = None,
    before: date | None = None,
    part_of_day: str | None = None,
    patient_id: str | None = None,
    exclude_appointment_id: str | None = None,
    limit: int = 5,
    horizon_days: int = DEFAULT_BOOKING_HORIZON_DAYS,
    urgency_lead_minutes: int = DEFAULT_URGENCY_MIN_LEAD_MINUTES,
) -> SlotSearch:
    """Free slots for a treatment, earliest first, and why each practitioner has none."""
    now_local = _to_madrid(now)
    today = now_local.date()
    if part_of_day is not None and part_of_day not in PARTS_OF_DAY:
        raise SlotSearchError("bad_range")

    minutes = _treatment_minutes(conn, treatment)
    practitioners = _candidate_practitioners(conn, treatment, practitioner_id)
    window = _window(today, treatment, after, before, horizon_days)
    diary = _load_diary(conn, practitioners, window, patient_id, exclude_appointment_id)

    # The lead time is real minutes, so it is measured on the UTC timeline: wall-clock
    # arithmetic over a clock change is an hour out, and two datetimes sharing one
    # ZoneInfo compare by wall clock, not by instant.
    lead_cutoff = now.astimezone(UTC) + timedelta(minutes=urgency_lead_minutes)
    slots: list[Slot] = []
    blocked: list[Blocked] = []

    for pid in practitioners:
        found: list[Slot] = []
        reasons: dict[str, _ReasonDays] = {}
        for day in window.days():
            candidates, reason, until = _day_slots(
                diary,
                pid=pid,
                day=day,
                minutes=minutes,
                is_today=day == today,
                treatment=treatment,
                lead_cutoff=lead_cutoff,
                part_of_day=part_of_day,
                patient_id=patient_id,
            )
            for candidate in candidates:
                found.append(
                    Slot(
                        practitioner_id=pid,
                        treatment=treatment,
                        start=candidate.start.astimezone(MADRID),
                        end=candidate.end.astimezone(MADRID),
                        same_day=day == today,
                    )
                )
            if reason is not None:
                reasons.setdefault(reason, _ReasonDays()).add(day, until)
        if found:
            slots.extend(found)
            continue
        if window.beyond_horizon:
            reasons.setdefault(BEYOND_HORIZON, _ReasonDays())
        blocked.append(Blocked(practitioner_id=pid, reasons=_order_reasons(reasons)))

    slots.sort(key=lambda s: (s.start.astimezone(UTC), s.practitioner_id))
    return SlotSearch(
        slots=slots[:limit],
        blocked=blocked,
        window_start=window.start,
        window_end=window.end,
    )


def check_slot(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    practitioner_id: str,
    treatment: str,
    start: datetime,
    patient_id: str | None = None,
    exclude_appointment_id: str | None = None,
    horizon_days: int = DEFAULT_BOOKING_HORIZON_DAYS,
    urgency_lead_minutes: int = DEFAULT_URGENCY_MIN_LEAD_MINUTES,
) -> str | None:
    """Re-check one concrete slot. `None` means bookable, otherwise a refusal code.

    A practitioner who does not perform the treatment (or is not in the clinic at all)
    gives `treatment_mismatch`; a treatment the clinic does not offer has no duration,
    so it raises `SlotSearchError` as a search does.
    """
    now_local = _to_madrid(now)
    start_local = _to_madrid(start)
    now_utc = now.astimezone(UTC)
    start_utc = start.astimezone(UTC)
    today = now_local.date()
    day = start_local.date()

    minutes = _treatment_minutes(conn, treatment)
    if practitioner_id not in _able_practitioners(conn, treatment):
        return "treatment_mismatch"

    # A Madrid wall time the clocks skip is not an instant at all, yet converting it
    # quietly moves it to one, so refuse it while the caller's own reading is still there.
    wall = _madrid_reading(start)
    if wall is not None and _instant(wall) is None:
        return "slot_taken"

    if day == today:
        if treatment != URGENCY:
            return "same_day"
        if start_utc < now_utc + timedelta(minutes=urgency_lead_minutes):
            return "too_soon"
    elif day < today or start_utc < now_utc:
        return "slot_taken"
    if day > today + timedelta(days=horizon_days):
        return "slot_taken"

    window = _Window(start=day, end=day, beyond_horizon=False)
    diary = _load_diary(conn, [practitioner_id], window, patient_id, exclude_appointment_id)
    candidates, _, _ = _day_slots(
        diary,
        pid=practitioner_id,
        day=day,
        minutes=minutes,
        is_today=day == today,
        treatment=treatment,
        lead_cutoff=now_utc,
        part_of_day=None,
        patient_id=None,
    )
    # The instant must be one the search itself would offer. A start with the same wall
    # clock but another offset is another instant, so it is not among them.
    taken = next((c for c in candidates if c.start == start_utc), None)
    if taken is None:
        return "slot_taken"
    if patient_id is not None and _overlaps(
        diary.patient_busy.get(day, ()), taken.start, taken.end
    ):
        return "patient_overlap"
    return None


# --- window ----------------------------------------------------------------


@dataclass(frozen=True)
class _Window:
    start: date
    end: date
    beyond_horizon: bool

    def days(self) -> list[date]:
        out: list[date] = []
        day = self.start
        while day <= self.end:
            out.append(day)
            day += timedelta(days=1)
        return out


def _window(
    today: date,
    treatment: str,
    after: date | None,
    before: date | None,
    horizon_days: int,
) -> _Window:
    first_bookable = today if treatment == URGENCY else today + timedelta(days=1)
    horizon_end = today + timedelta(days=horizon_days)
    wanted_start = first_bookable if after is None else after
    # With no `before`, an `after` past the horizon is not a bad range: it reports
    # `beyond_horizon` for every practitioner, so the agent can explain the limit.
    wanted_end = max(horizon_end, wanted_start) if before is None else before
    if wanted_start > wanted_end or wanted_end < first_bookable:
        raise SlotSearchError("bad_range")
    return _Window(
        start=max(wanted_start, first_bookable),
        end=min(wanted_end, horizon_end),
        beyond_horizon=wanted_end > horizon_end,
    )


# --- diary -----------------------------------------------------------------


@dataclass(frozen=True)
class _Diary:
    hours: dict[str, dict[int, list[tuple[int, int]]]]
    closures: set[str]
    whole_day_absences: dict[str, list[tuple[date, date, str]]]
    part_day_absences: dict[str, list[tuple[date, date, int, int, str]]]
    booked: dict[tuple[str, date], list[tuple[datetime, datetime]]]
    patient_busy: dict[date, list[tuple[datetime, datetime]]]


def _load_diary(
    conn: sqlite3.Connection,
    practitioners: list[str],
    window: _Window,
    patient_id: str | None,
    exclude_appointment_id: str | None,
) -> _Diary:
    marks = ",".join("?" for _ in practitioners)
    first, last = window.start, window.end

    hours: dict[str, dict[int, list[tuple[int, int]]]] = {p: {} for p in practitioners}
    closures: set[str] = set()
    whole_day: dict[str, list[tuple[date, date, str]]] = {p: [] for p in practitioners}
    part_day: dict[str, list[tuple[date, date, int, int, str]]] = {p: [] for p in practitioners}
    booked: dict[tuple[str, date], list[tuple[datetime, datetime]]] = {}
    patient_busy: dict[date, list[tuple[datetime, datetime]]] = {}
    if not practitioners or first > last:
        return _Diary(hours, closures, whole_day, part_day, booked, patient_busy)

    for pid, weekday, start_local, end_local in conn.execute(
        "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
        f" WHERE practitioner_id IN ({marks})",
        practitioners,
    ):
        hours[pid].setdefault(weekday, []).append((_minutes(start_local), _minutes(end_local)))
    for by_weekday in hours.values():
        for intervals in by_weekday.values():
            intervals.sort()

    closures = {
        row[0]
        for row in conn.execute(
            "SELECT date FROM closures WHERE date BETWEEN ? AND ?",
            (first.isoformat(), last.isoformat()),
        )
    }

    for pid, start_date, end_date, start_at, end_at in conn.execute(
        "SELECT practitioner_id, start_date, end_date, start_local, end_local FROM absences"
        f" WHERE practitioner_id IN ({marks}) AND start_date <= ? AND end_date >= ?",
        [*practitioners, last.isoformat(), first.isoformat()],
    ):
        span = (_parse_date(start_date), _parse_date(end_date))
        if start_at is None or end_at is None:
            whole_day[pid].append((*span, end_date))
        else:
            part_day[pid].append((*span, _minutes(start_at), _minutes(end_at), end_date))

    # One day of slack below, so an appointment that started the day before is still seen.
    low = _utc_text(_wall(first - timedelta(days=1), 0).replace(tzinfo=MADRID))
    high = _utc_text(_wall(last + timedelta(days=1), 0).replace(tzinfo=MADRID))
    for pid, aid, start_utc, end_utc in conn.execute(
        "SELECT practitioner_id, appointment_id, start_utc, end_utc FROM appointments"
        f" WHERE practitioner_id IN ({marks}) AND status = 'booked'"
        " AND start_utc >= ? AND start_utc < ?",
        [*practitioners, low, high],
    ):
        if aid == exclude_appointment_id:
            continue
        start, end = _parse_utc(start_utc), _parse_utc(end_utc)
        for busy_day in _local_days(start, end):
            booked.setdefault((pid, busy_day), []).append((start, end))

    if patient_id is not None:
        for aid, start_utc, end_utc in conn.execute(
            "SELECT appointment_id, start_utc, end_utc FROM appointments"
            " WHERE patient_id = ? AND status = 'booked' AND start_utc >= ? AND start_utc < ?",
            (patient_id, low, high),
        ):
            if aid == exclude_appointment_id:
                continue
            start, end = _parse_utc(start_utc), _parse_utc(end_utc)
            for busy_day in _local_days(start, end):
                patient_busy.setdefault(busy_day, []).append((start, end))

    return _Diary(hours, closures, whole_day, part_day, booked, patient_busy)


# --- one practitioner on one day -------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    """One possible start: the wall-clock minute of the day it reads, and its instants."""

    start_min: int
    start: datetime  # UTC
    end: datetime  # UTC


def _day_slots(
    diary: _Diary,
    *,
    pid: str,
    day: date,
    minutes: int,
    is_today: bool,
    treatment: str,
    lead_cutoff: datetime,
    part_of_day: str | None,
    patient_id: str | None,
) -> tuple[list[_Candidate], str | None, str | None]:
    """Surviving candidates, plus the reason (and `until`) that removed the last one."""
    if day.isoformat() in diary.closures:
        return [], CLINIC_CLOSED, None

    until = _whole_day_absence(diary, pid, day)
    if until is not None:
        return [], ABSENT, until

    starts = _candidates(day, diary.hours.get(pid, {}).get(day.weekday(), ()), minutes)
    if not starts:
        return [], NOT_WORKING_THAT_DAY, None

    kept: list[_Candidate] = []
    absence_until: str | None = None
    for candidate in starts:
        hit = _part_day_absence(diary, pid, day, candidate)
        if hit is None:
            kept.append(candidate)
        elif absence_until is None or hit > absence_until:
            absence_until = hit
    if not kept:
        return [], ABSENT, absence_until
    starts = kept

    busy = diary.booked.get((pid, day), ())
    starts = [c for c in starts if not _overlaps(busy, c.start, c.end)]
    if not starts:
        return [], FULLY_BOOKED, None

    if is_today and treatment == URGENCY:
        starts = [c for c in starts if c.start >= lead_cutoff]
        if not starts:
            return [], WITHIN_LEAD_TIME, None

    if part_of_day is not None:
        noon = _minutes(AFTERNOON_STARTS)
        starts = [c for c in starts if (c.start_min < noon) == (part_of_day == "morning")]
        if not starts:
            return [], OUTSIDE_PART_OF_DAY, None

    if patient_id is not None:
        patient_busy = diary.patient_busy.get(day, ())
        starts = [c for c in starts if not _overlaps(patient_busy, c.start, c.end)]
        if not starts:
            return [], PATIENT_BUSY, None

    return starts, None, None


def _candidates(day: date, intervals: Any, minutes: int) -> list[_Candidate]:
    """Every start the grid offers that day, each one once, earliest instant first.

    Overlapping, touching or repeated `working_hours` rows are merged into disjoint
    intervals before the grid is laid out, so a treatment may run across the join of
    two of them (09:00-12:00 with 11:30-13:00 offers an hour from 11:30) and every
    start, kept in a set, is offered once however many rows cover it.

    A start whose Madrid wall clock does not exist or happens twice that day is skipped.
    Length is real minutes on the UTC timeline, so a slot may span the hour the spring
    change skips: 01:30 plus an hour ends at 03:30+02:00, one real hour later. Such a
    slot is kept, and dropped only when that real end passes the end of the shift.
    """
    by_instant: dict[datetime, int] = {}
    for start_min, end_min in _merge_intervals(intervals):
        first = -(-start_min // GRID_MINUTES) * GRID_MINUTES
        for candidate_min in range(first, end_min - minutes + 1, GRID_MINUTES):
            start = _instant(_wall(day, candidate_min))
            if start is None:
                continue
            end = start + timedelta(minutes=minutes)
            if _wall_minutes(day, end.astimezone(MADRID)) > end_min:
                continue
            by_instant[start] = candidate_min
    return [
        _Candidate(
            start_min=by_instant[start],
            start=start,
            end=start + timedelta(minutes=minutes),
        )
        for start in sorted(by_instant)
    ]


def _merge_intervals(intervals: Any) -> list[tuple[int, int]]:
    """Working hours as disjoint wall-clock intervals: overlapping or touching rows join."""
    merged: list[tuple[int, int]] = []
    for start_min, end_min in sorted(intervals):
        if merged and start_min <= merged[-1][1]:
            joined_start, joined_end = merged[-1]
            merged[-1] = (joined_start, max(joined_end, end_min))
        else:
            merged.append((start_min, end_min))
    return merged


def _whole_day_absence(diary: _Diary, pid: str, day: date) -> str | None:
    until: str | None = None
    for start_date, end_date, end_text in diary.whole_day_absences.get(pid, ()):
        if start_date <= day <= end_date and (until is None or end_text > until):
            until = end_text
    return until


def _part_day_absence(diary: _Diary, pid: str, day: date, candidate: _Candidate) -> str | None:
    """The `until` of the part-day absence hiding this candidate, or None when it is free."""
    until: str | None = None
    end_min = _wall_minutes(day, candidate.end.astimezone(MADRID))
    for start_date, end_date, from_min, to_min, end_text in diary.part_day_absences.get(pid, ()):
        covered = (
            start_date <= day <= end_date and candidate.start_min < to_min and end_min > from_min
        )
        if covered and (until is None or end_text > until):
            until = end_text
    return until


def _overlaps(busy: Any, start: datetime, end: datetime) -> bool:
    """True when a booked span covers part of [start, end). Every datetime here is UTC."""
    return any(b_start < end and b_end > start for b_start, b_end in busy)


# --- reasons ---------------------------------------------------------------


class _ReasonDays:
    def __init__(self) -> None:
        self.dates: list[str] = []
        self.until: str | None = None

    def add(self, day: date, until: str | None) -> None:
        self.dates.append(day.isoformat())
        if until is not None and (self.until is None or until > self.until):
            self.until = until


def _order_reasons(reasons: dict[str, _ReasonDays]) -> list[BlockReason]:
    def sort_key(item: tuple[str, _ReasonDays]) -> tuple[int, str]:
        code, days = item
        if code == BEYOND_HORIZON:
            return (1, "")
        return (0, days.dates[0] if days.dates else "")

    return [
        BlockReason(reason=code, dates=sorted(days.dates), until=days.until)
        for code, days in sorted(reasons.items(), key=sort_key)
    ]


# --- lookups and time ------------------------------------------------------


def _treatment_minutes(conn: sqlite3.Connection, treatment: str) -> int:
    row = conn.execute(
        "SELECT minutes FROM treatments WHERE treatment_id = ?", (treatment,)
    ).fetchone()
    if row is None:
        raise SlotSearchError("unknown_treatment")
    return int(row[0])


def _able_practitioners(conn: sqlite3.Connection, treatment: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT practitioner_id FROM practitioner_treatments WHERE treatment_id = ?"
            " ORDER BY practitioner_id",
            (treatment,),
        )
    ]


def _candidate_practitioners(
    conn: sqlite3.Connection, treatment: str, practitioner_id: str | None
) -> list[str]:
    able = _able_practitioners(conn, treatment)
    if practitioner_id is None:
        return able
    known = conn.execute(
        "SELECT 1 FROM practitioners WHERE practitioner_id = ?", (practitioner_id,)
    ).fetchone()
    if known is None:
        raise SlotSearchError("unknown_practitioner")
    if practitioner_id not in able:
        raise SlotSearchError("treatment_mismatch")
    return [practitioner_id]


def _to_madrid(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("a naive datetime has no meaning here: pass an aware one")
    return value.astimezone(MADRID)


def _wall(day: date, minute_of_day: int) -> datetime:
    """A Madrid wall-clock reading, as minutes past midnight; it may pass midnight."""
    whole_days, rest = divmod(minute_of_day, _MINUTES_PER_DAY)
    day += timedelta(days=whole_days)
    return datetime(day.year, day.month, day.day, rest // 60, rest % 60)  # noqa: DTZ001


def _wall_minutes(day: date, moment: datetime) -> int:
    """A Madrid time read as minutes past midnight of `day`, which it may pass."""
    return (moment.date() - day).days * _MINUTES_PER_DAY + moment.hour * 60 + moment.minute


def _instant(wall: datetime) -> datetime | None:
    """The UTC instant of a Madrid wall-clock reading, or None when it is no instant.

    On a clock-change day the two `fold` readings of a wall time disagree on the offset,
    and the reading either does not exist (the spring change skips it, so it comes back
    as another time from a round trip through UTC) or happens twice (the autumn change
    repeats it). Neither can be a slot, so both give None; no date is written down here.
    """
    early = wall.replace(tzinfo=MADRID)
    late = wall.replace(tzinfo=MADRID, fold=1)
    moment = early.astimezone(UTC)
    exists = moment.astimezone(MADRID).replace(tzinfo=None) == wall
    repeated = early.utcoffset() != late.utcoffset()
    if not exists or repeated:
        return None
    return moment


def _madrid_reading(moment: datetime) -> datetime | None:
    """The caller's own wall clock, when they gave a Europe/Madrid time; else None."""
    tz = moment.tzinfo
    if isinstance(tz, ZoneInfo) and tz.key == "Europe/Madrid":
        return moment.replace(tzinfo=None)
    return None


def _local_days(start: datetime, end: datetime) -> set[date]:
    """The Madrid dates a booked span touches, so one over midnight is seen on both."""
    return {start.astimezone(MADRID).date(), end.astimezone(MADRID).date()}


def _minutes(hhmm: str) -> int:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return hours * 60 + minutes


def _parse_date(text: str) -> date:
    year, month, day = (int(part) for part in text.split("-"))
    return date(year, month, day)


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, _UTC_FMT).replace(tzinfo=UTC)


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_UTC_FMT)
