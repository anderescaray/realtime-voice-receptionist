"""Local/UTC conversion for Europe/Madrid (no wall-clock reads)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
UTC_FMT_LEN = 20  # YYYY-MM-DDTHH:MM:SSZ


def parse_local_date(text: str) -> date:
    y, m, d = (int(x) for x in text.split("-"))
    return date(y, m, d)


def weekday_local(d: date) -> int:
    return d.weekday()


def local_datetime(d: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=MADRID)


def to_utc_text(dt_local: datetime) -> str:
    return dt_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_minutes_local(d: date, hhmm: str, minutes: int) -> datetime:
    start = local_datetime(d, hhmm)
    return start + timedelta(minutes=minutes)


def utc_range_ok(start_utc: str, end_utc: str) -> bool:
    return len(start_utc) == UTC_FMT_LEN and len(end_utc) == UTC_FMT_LEN and end_utc > start_utc


def quarter_hours_between(start_local: str, end_local: str) -> list[str]:
    """Start times on the 15-minute grid in [start, end)."""
    h0, m0 = (int(x) for x in start_local.split(":"))
    h1, m1 = (int(x) for x in end_local.split(":"))
    start_mins = h0 * 60 + m0
    end_mins = h1 * 60 + m1
    out: list[str] = []
    t = start_mins
    while t < end_mins:
        out.append(f"{t // 60:02d}:{t % 60:02d}")
        t += 15
    return out
