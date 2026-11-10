"""Deterministic appointment diary."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from seed import catalogue
from seed.timeutil import (
    add_minutes_local,
    local_datetime,
    parse_local_date,
    quarter_hours_between,
    to_utc_text,
)

TREATMENT_WEIGHTS = (
    ("checkup", 35),
    ("cleaning", 30),
    ("filling", 15),
    ("extraction", 8),
    ("ortho_consult", 4),
)

WEEK_OCCUPANCY = {
    1: (0.60, 0.75),
    2: (0.40, 0.55),
    3: (0.25, 0.40),
    4: (0.10, 0.25),
}
HISTORY_OCCUPANCY = (0.50, 0.70)

CANCEL_SHARE_LOW = 0.04
CANCEL_SHARE_HIGH = 0.07
URGENCY_HISTORY_LOW = 0.03
URGENCY_HISTORY_HIGH = 0.06

FULLY_BOOKED_PRACTITIONER = "dent_1"


def _practitioner_treatments() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pid, tid in catalogue.PRACTITIONER_TREATMENTS:
        out.setdefault(pid, []).append(tid)
    for pid in out:
        out[pid].sort()
    return out


def _hours_by_weekday() -> dict[str, dict[int, list[tuple[str, str]]]]:
    out: dict[str, dict[int, list[tuple[str, str]]]] = {}
    for pid, wd, start, end in catalogue.WORKING_HOURS:
        out.setdefault(pid, {}).setdefault(wd, []).append((start, end))
    return out


def _absence_ranges(anchor: date) -> dict[str, list[tuple[date, date]]]:
    out: dict[str, list[tuple[date, date]]] = {}
    for row in catalogue.absences_for_anchor(anchor):
        start = parse_local_date(row["start_date"])
        end = parse_local_date(row["end_date"])
        out.setdefault(row["practitioner_id"], []).append((start, end))
    return out


def _on_absence(pid: str, d: date, absence: dict[str, list[tuple[date, date]]]) -> bool:
    return any(start <= d <= end for start, end in absence.get(pid, []))


def _day_working_minutes(pid: str, d: date, hours: dict) -> int:
    wd = d.weekday()
    total = 0
    for seg_start, seg_end in hours[pid].get(wd, []):
        total += len(quarter_hours_between(seg_start, seg_end)) * 15
    return total


def _treatment_fits(
    pid: str, d: date, start_hhmm: str, tid: str, hours: dict, minutes: int
) -> bool:
    wd = d.weekday()
    end_dt = add_minutes_local(d, start_hhmm, minutes)
    end_h = end_dt.hour
    end_m = end_dt.minute
    end_hhmm = f"{end_h:02d}:{end_m:02d}"
    for seg_start, seg_end in hours[pid].get(wd, []):
        if seg_start <= start_hhmm and end_hhmm <= seg_end:
            return True
    return False


def _day_slots(pid: str, d: date, hours: dict) -> list[str]:
    wd = d.weekday()
    slots: list[str] = []
    for seg_start, seg_end in hours[pid].get(wd, []):
        slots.extend(quarter_hours_between(seg_start, seg_end))
    return slots


def _pick_fully_booked_day(
    anchor: date,
    pid: str,
    hours: dict,
    absence: dict[str, list[tuple[date, date]]],
    closures: frozenset[str],
) -> date | None:
    # Start two days after the anchor: the anchor is "today" for calls on it, and a
    # fixture day must be bookable and distinct from "tomorrow". Fall back over two weeks.
    for offset in range(2, 14):
        d = anchor + timedelta(days=offset)
        if d.isoformat() in closures or _on_absence(pid, d, absence):
            continue
        if _day_slots(pid, d, hours):
            return d
    return None


# Fixed mix for the fully booked fixture day: mostly check-ups and fillings.
_FIXTURE_ROTATION = ("checkup", "filling", "cleaning", "checkup", "extraction", "filling")


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _tile_full_day(
    pid: str,
    d: date,
    hours: dict,
    allowed: list[str],
) -> list[tuple[str, str]]:
    """Cover every 15-minute cell of the day with a realistic mix of treatments.

    Walks a fixed rotation and takes the first treatment that leaves either nothing
    or at least 30 minutes of the shift, so no uncoverable 15-minute gap remains.
    """
    rotation = [t for t in _FIXTURE_ROTATION if t in allowed]
    plan: list[tuple[str, str]] = []
    turn = 0
    for seg_start, seg_end in hours[pid].get(d.weekday(), []):
        cursor = _hhmm_to_minutes(seg_start)
        end = _hhmm_to_minutes(seg_end)
        while cursor < end:
            left = end - cursor
            for step in range(len(rotation)):
                tid = rotation[(turn + step) % len(rotation)]
                mins = catalogue.TREATMENT_MINUTES[tid]
                if mins == left or left - mins >= 30:
                    break
            else:
                raise ValueError(f"cannot tile {left} minutes for {pid} on {d}")
            plan.append((f"{cursor // 60:02d}:{cursor % 60:02d}", tid))
            cursor += mins
            turn += step + 1
    return plan


def _pick_treatment(rng: random.Random, allowed: list[str]) -> str:
    weights = [(t, w) for t, w in TREATMENT_WEIGHTS if t in allowed]
    total = sum(w for _, w in weights)
    r = rng.randint(1, total)
    acc = 0
    for tid, w in weights:
        acc += w
        if r <= acc:
            return tid
    return weights[-1][0]


def _occupancy_target(is_future: bool, d: date, anchor: date) -> float:
    if not is_future:
        lo, hi = HISTORY_OCCUPANCY
    else:
        week = min(4, (d - anchor).days // 7 + 1)
        lo, hi = WEEK_OCCUPANCY[week]
    return (lo + hi) / 2


def _want_cancelled(rng: random.Random, total: int, cancelled: int) -> bool:
    if total == 0:
        return rng.randint(1, 100) <= 5
    rate = cancelled / total
    if rate < CANCEL_SHARE_LOW:
        return rng.randint(1, 100) <= 10
    if rate > CANCEL_SHARE_HIGH:
        return rng.randint(1, 100) <= 2
    return rng.randint(1, 100) <= 5


def _want_history_urgency(rng: random.Random, history_rows: int, urgency_rows: int) -> bool:
    if history_rows == 0:
        return True
    share = urgency_rows / history_rows
    if share < URGENCY_HISTORY_LOW:
        return rng.randint(1, 100) <= 25
    if share > URGENCY_HISTORY_HIGH:
        return False
    return rng.randint(1, 100) <= 8


def generate_appointments(
    anchor: date,
    seed: int,
    patient_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed + 991)
    hours = _hours_by_weekday()
    ptreat = _practitioner_treatments()
    absence = _absence_ranges(anchor)
    practitioners = sorted(ptreat.keys())

    diary_start = anchor
    diary_end = anchor + timedelta(days=27)
    history_start = anchor - timedelta(days=14)

    fully_day = _pick_fully_booked_day(
        anchor, FULLY_BOOKED_PRACTITIONER, hours, absence, catalogue.CLOSURE_DATES
    )
    fully_day_iso = fully_day.isoformat() if fully_day else ""

    booked_pr: dict[str, list[tuple[str, str]]] = {p: [] for p in practitioners}
    booked_pt: dict[str, list[tuple[str, str]]] = {}
    future_count: dict[str, int] = {pid: 0 for pid in patient_ids}
    booked_day_minutes: dict[tuple[str, date], int] = {}
    appointments: list[dict[str, Any]] = []
    appt_total = 0
    appt_cancelled = 0
    history_rows = 0
    history_urgency_rows = 0

    def overlap(intervals: list[tuple[str, str]], start_u: str, end_u: str) -> bool:
        return any(s < end_u and e > start_u for s, e in intervals)

    def booked_would_fit(
        pid: str,
        pat: str,
        d: date,
        start_hhmm: str,
        tid: str,
        *,
        enforce_future_cap: bool,
    ) -> bool:
        minutes = catalogue.TREATMENT_MINUTES[tid]
        if not _treatment_fits(pid, d, start_hhmm, tid, hours, minutes):
            return False
        start_u = to_utc_text(local_datetime(d, start_hhmm))
        end_u = to_utc_text(add_minutes_local(d, start_hhmm, minutes))
        if overlap(booked_pr[pid], start_u, end_u):
            return False
        if overlap(booked_pt.setdefault(pat, []), start_u, end_u):
            return False
        return not (enforce_future_cap and future_count[pat] >= 3)

    def commit_row(
        pid: str,
        pat: str,
        d: date,
        start_hhmm: str,
        tid: str,
        *,
        status: str,
        is_future: bool,
        same_day_urgent: int,
    ) -> None:
        nonlocal appt_total, appt_cancelled, history_rows, history_urgency_rows
        minutes = catalogue.TREATMENT_MINUTES[tid]
        start_u = to_utc_text(local_datetime(d, start_hhmm))
        end_u = to_utc_text(add_minutes_local(d, start_hhmm, minutes))
        row = {
            "patient_id": pat,
            "practitioner_id": pid,
            "treatment_id": tid,
            "start_utc": start_u,
            "end_utc": end_u,
            "status": status,
            "same_day_urgent": same_day_urgent,
            "created_by_call": None,
        }
        appointments.append(row)
        appt_total += 1
        if not is_future:
            history_rows += 1
            if tid == "urgency":
                history_urgency_rows += 1
        if status == "cancelled":
            appt_cancelled += 1
        elif status == "booked":
            booked_pr[pid].append((start_u, end_u))
            booked_pt.setdefault(pat, []).append((start_u, end_u))
            if is_future:
                future_count[pat] += 1
            key = (pid, d)
            booked_day_minutes[key] = booked_day_minutes.get(key, 0) + minutes

    def try_add(
        pid: str,
        pat: str,
        d: date,
        start_hhmm: str,
        tid: str,
        *,
        is_future: bool,
        allow_extra_future: bool = False,
        force_status: str | None = None,
        same_day_urgent: int = 0,
    ) -> bool:
        minutes = catalogue.TREATMENT_MINUTES[tid]
        if not _treatment_fits(pid, d, start_hhmm, tid, hours, minutes):
            return False
        if is_future and not allow_extra_future and pat in no_future_patients:
            return False
        check_future_cap = is_future and not allow_extra_future
        can_book = booked_would_fit(
            pid, pat, d, start_hhmm, tid, enforce_future_cap=check_future_cap
        )
        if not can_book and force_status != "booked":
            return False
        if force_status is not None:
            status = force_status
        elif not can_book:
            return False
        else:
            status = "cancelled" if _want_cancelled(rng, appt_total, appt_cancelled) else "booked"
        if status == "booked" and not can_book:
            return False
        commit_row(
            pid,
            pat,
            d,
            start_hhmm,
            tid,
            status=status,
            is_future=is_future,
            same_day_urgent=same_day_urgent,
        )
        return True

    n_no_future = rng.randint(
        int(len(patient_ids) * 0.40),
        int(len(patient_ids) * 0.50),
    )
    no_future_patients = set(sorted(rng.sample(patient_ids, n_no_future)))

    if fully_day is not None:
        full_day_plan = _tile_full_day(
            FULLY_BOOKED_PRACTITIONER,
            fully_day,
            hours,
            ptreat[FULLY_BOOKED_PRACTITIONER],
        )
        for i, (start_hhmm, tid) in enumerate(full_day_plan):
            pat = patient_ids[i % len(patient_ids)]
            try_add(
                FULLY_BOOKED_PRACTITIONER,
                pat,
                fully_day,
                start_hhmm,
                tid,
                is_future=True,
                allow_extra_future=True,
                force_status="booked",
            )

    day = history_start
    end_day = diary_end
    pat_idx = 0

    while day <= end_day:
        iso = day.isoformat()
        is_future = day >= diary_start
        if iso in catalogue.CLOSURE_DATES:
            day += timedelta(days=1)
            continue
        for pid in practitioners:
            if _on_absence(pid, day, absence):
                continue
            if fully_day is not None and day == fully_day and pid == FULLY_BOOKED_PRACTITIONER:
                continue
            wd_slots = _day_slots(pid, day, hours)
            if not wd_slots:
                continue
            work_mins = _day_working_minutes(pid, day, hours)
            target_occ = _occupancy_target(is_future, day, anchor)
            target_mins = int(work_mins * target_occ)
            booked_mins = booked_day_minutes.get((pid, day), 0)
            tries = 0
            while booked_mins < target_mins and tries < len(wd_slots) * 6:
                tries += 1
                start_hhmm = wd_slots[rng.randint(0, len(wd_slots) - 1)]
                allowed = ptreat[pid]
                if (
                    not is_future
                    and "urgency" in allowed
                    and _want_history_urgency(rng, history_rows, history_urgency_rows)
                ):
                    tid = "urgency"
                    urgent = 1
                else:
                    future_allowed = [t for t in allowed if t != "urgency"]
                    tid = _pick_treatment(rng, future_allowed)
                    urgent = 0
                pat = patient_ids[pat_idx % len(patient_ids)]
                pat_idx += 1
                if try_add(
                    pid,
                    pat,
                    day,
                    start_hhmm,
                    tid,
                    is_future=is_future,
                    same_day_urgent=urgent,
                ):
                    booked_mins = booked_day_minutes.get((pid, day), 0)
        day += timedelta(days=1)

    appointments.sort(key=lambda r: (r["start_utc"], r["practitioner_id"], r["patient_id"]))
    for i, row in enumerate(appointments, start=1):
        row["appointment_id"] = f"a{i:05d}"

    diary_fixtures: dict[str, Any] = {}
    if fully_day is not None:
        diary_fixtures["fully_booked"] = {
            "date": fully_day_iso,
            "practitioner_id": FULLY_BOOKED_PRACTITIONER,
        }
    return appointments, diary_fixtures
