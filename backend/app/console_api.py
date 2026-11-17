"""Read-only staff console HTTP API."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import wave
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import config

CALL_ID_RE = re.compile(r"^[0-9a-f]{12}$")

OUTCOME_FLAGS = frozenset(
    {
        "emergency",
        "provider_quota",
        "voice_unresponsive",
        "call_cap",
        "brain_error",
        "same_day_urgent",
        "urgent_callback",
        "readback_mismatch",
    }
)

CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _num(value: Any) -> float | None:
    """A finite number from a log field, or None for anything else (bools included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> int:
    """A non-negative whole count from a log field; anything malformed counts as 0."""
    n = _num(value)
    return int(n) if n is not None and n >= 0 else 0


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"non-finite number {text}")
    return number


def _loads(text: str) -> Any:
    """json.loads that refuses NaN, Infinity and overflowing floats such as 1e999."""
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)


def _instant(iso: str | None) -> float | None:
    """Seconds since the epoch for an ISO timestamp with an offset (or Z), else None."""
    if iso is None:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def linear_percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile on a sorted sample (p in 0..100).

    Uses the common method: index = (p/100) * (n - 1), interpolate between
    floor and ceiling ranks. Returns None for an empty list.
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def connect_db_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError("diary database not available")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Valid events in file order, and how many lines were skipped.

    A call still being written may end with a half line; it is skipped like any
    other line that is not a JSON object with a numeric `t` and a string `kind`.
    Raises OSError when the file cannot be read (for example, it was just purged).
    """
    events: list[dict[str, Any]] = []
    bad_lines = 0
    # Read in one call, so a file removed meanwhile raises instead of looking empty.
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            obj = _loads(line)
        except ValueError:
            bad_lines += 1
            continue
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("t"), (int, float))
            or not isinstance(obj.get("kind"), str)
        ):
            bad_lines += 1
            continue
        events.append(obj)
    return events, bad_lines


def list_call_files(calls_dir: Path) -> list[Path]:
    if not calls_dir.is_dir():
        return []
    out: list[Path] = []
    for entry in calls_dir.iterdir():
        name = entry.name
        if not name.endswith(".jsonl"):
            continue
        path = _safe_child_file(calls_dir, name[:-6], ".jsonl")
        if path is not None:
            out.append(path)
    return out


def _ended_by(events: list[dict[str, Any]]) -> str | None:
    for ev in reversed(events):
        k = ev.get("kind")
        if k == "caller_hung_up":
            return "caller"
        if k == "call_cap_reached":
            return "cap"
        if k == "agent_hung_up":
            return "agent"
    return None


def _derive_flags_from_events(events: list[dict[str, Any]]) -> set[str]:
    flags: set[str] = set()
    for ev in events:
        kind = ev.get("kind")
        if kind == "provider_error" and ev.get("category") == "quota":
            flags.add("provider_quota")
        elif kind == "voice_unresponsive":
            flags.add("voice_unresponsive")
        elif kind == "readback_mismatch":
            flags.add("readback_mismatch")
        elif kind == "call_cap_reached":
            flags.add("call_cap")
        elif kind == "message_taken" and ev.get("urgent") is True:
            flags.add("urgent_callback")
    return flags


def _count_fields(events: list[dict[str, Any]], bad_lines: int) -> dict[str, int]:
    counts = {
        "caller_turns": 0,
        "agent_turns": 0,
        "echo_transcripts": 0,
        "transcripts_missing": 0,
        "readback_mismatches": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "provider_errors": 0,
        "bad_lines": bad_lines,
    }
    for ev in events:
        kind = ev.get("kind")
        if kind == "transcript":
            role = ev.get("role")
            if role == "caller":
                if ev.get("echo"):
                    counts["echo_transcripts"] += 1
                else:
                    counts["caller_turns"] += 1
            elif role == "agent":
                counts["agent_turns"] += 1
        elif kind == "transcript_missing":
            counts["transcripts_missing"] += 1
        elif kind == "readback_mismatch":
            counts["readback_mismatches"] += 1
        elif kind == "tool_call":
            counts["tool_calls"] += 1
        elif kind == "tool_result" and ev.get("ok") is False:
            counts["tool_errors"] += 1
        elif kind == "provider_error":
            counts["provider_errors"] += 1
    return counts


def _wav_secs(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return wf.getnframes() / float(rate)
    except (wave.Error, EOFError, OSError, ValueError):
        return None


def _audio_info(recordings_dir: Path, call_id: str) -> dict[str, Any]:
    wav = _safe_child_file(recordings_dir, call_id, ".wav")
    if wav is None:
        return {"available": False, "secs": None}
    secs = _wav_secs(wav)
    return {"available": True, "secs": secs}


def _safe_child_file(base: Path, call_id: str, suffix: str) -> Path | None:
    if not CALL_ID_RE.fullmatch(call_id):
        return None
    base_resolved = base.resolve()
    direct = base / f"{call_id}{suffix}"
    if direct.is_symlink():
        return None
    candidate = direct.resolve()
    if not candidate.is_file():
        return None
    if candidate.parent != base_resolved:
        return None
    return candidate


def _read_timing(path: Path) -> dict[str, Any] | None:
    """The timing file as an object, or None when it is unreadable or not an object."""
    try:
        data = _loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _overlap_rate(answered: int, overlapped: int) -> float | None:
    denom = answered + overlapped
    if denom == 0:
        return None
    return overlapped / denom


def _format_overlap_pct(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{round(rate * 100)}%"


def latency_text(
    p50: float | None,
    p90: float | None,
    answered: int,
    overlapped: int,
    unanswered: int,
    *,
    prefix: str = "",
    suffix: str = "",
) -> str:
    rate = _overlap_rate(answered, overlapped)
    otxt = _format_overlap_pct(rate)
    core = f"n = {answered} answered, overlap {otxt}, {unanswered} unanswered"
    if answered == 0:
        return f"{prefix}no answered turns ({core}){suffix}".strip()
    p50_s = str(round(p50)) if p50 is not None else "n/a"
    p90_s = str(round(p90)) if p90 is not None else "n/a"
    return f"{prefix}p50 {p50_s} / p90 {p90_s} ms ({core}){suffix}".strip()


def build_latency_block(metrics: dict[str, Any]) -> dict[str, Any]:
    answered = _count(metrics.get("answered_turns"))
    overlapped = _count(metrics.get("overlapped_turns"))
    turns = _count(metrics.get("turns"))
    skipped = _count(metrics.get("skipped_turns"))
    guard = _count(metrics.get("guard_firings"))
    unanswered = max(turns - answered - overlapped, 0)
    rate = _overlap_rate(answered, overlapped)
    p50_f = _num(metrics.get("latency_p50_ms"))
    p90_f = _num(metrics.get("latency_p90_ms"))
    if answered == 0:
        p50_f = None
        p90_f = None
    return {
        "which": "server",
        "p50_ms": p50_f,
        "p90_ms": p90_f,
        "answered": answered,
        "overlapped": overlapped,
        "overlap_rate": rate,
        "unanswered": unanswered,
        "skipped_turns": skipped,
        "guard_firings": guard,
        "text": latency_text(p50_f, p90_f, answered, overlapped, unanswered),
    }


def _last_audio_metrics(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for ev in events:
        if ev.get("kind") == "audio_metrics":
            last = ev
    if last is None:
        return None
    return {k: v for k, v in last.items() if k not in ("t", "kind")}


def _outcome_slice(ev: dict[str, Any] | None) -> dict[str, Any] | None:
    if ev is None:
        return None

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    return {
        "result": _str(ev.get("result")),
        "changes": as_list(ev.get("changes")),
        "no_action_reason": _str(ev.get("no_action_reason")),
        "escalations": as_list(ev.get("escalations")),
        "flags": [f for f in as_list(ev.get("flags")) if isinstance(f, str)],
    }


def build_summary(
    call_id: str,
    events: list[dict[str, Any]],
    bad_lines: int,
    recordings_dir: Path,
    same_day_urgent_calls: set[str] | None,
    file_mtime: float | None = None,
) -> dict[str, Any]:
    started: dict[str, Any] | None = None
    call_ended: dict[str, Any] | None = None
    outcome_ev: dict[str, Any] | None = None
    for ev in events:
        kind = ev.get("kind")
        if kind == "call_started" and started is None:
            started = ev
        elif kind == "call_ended":
            call_ended = ev
        elif kind == "outcome":
            outcome_ev = ev

    started_at = _str(started.get("now")) if started else None
    instant = _instant(started_at)
    sort_key = instant if instant is not None else (file_mtime or 0.0)

    flags: set[str] = _derive_flags_from_events(events)
    if outcome_ev and isinstance(outcome_ev.get("flags"), list):
        for f in outcome_ev["flags"]:
            if isinstance(f, str):
                flags.add(f)
    if same_day_urgent_calls and call_id in same_day_urgent_calls:
        flags.add("same_day_urgent")

    in_progress = call_ended is None
    duration = _num(call_ended.get("duration_secs")) if call_ended else None

    return {
        "call_id": call_id,
        "started_at": started_at,
        "channel": _str(started.get("channel")) if started else None,
        "voice": _str(started.get("voice")) if started else None,
        "language": _str(started.get("language")) if started else None,
        "schema": _num(started.get("schema")) if started else None,
        "in_progress": in_progress,
        "duration_secs": duration,
        "ended_by": _ended_by(events),
        "outcome": _outcome_slice(outcome_ev),
        "flags": sorted(flags),
        "counts": _count_fields(events, bad_lines),
        "audio": _audio_info(recordings_dir, call_id),
        "_sort_key": sort_key,
    }


def _load_same_day_urgent(db_path: Path) -> set[str]:
    try:
        with closing(connect_db_ro(db_path)) as conn:
            rows = conn.execute(
                "SELECT created_by_call FROM appointments "
                "WHERE status = 'booked' AND same_day_urgent = 1 AND created_by_call IS NOT NULL"
            ).fetchall()
            return {r[0] for r in rows if r[0]}
    except FileNotFoundError:
        return set()


def _strip_internal(summary: dict[str, Any]) -> dict[str, Any]:
    s = dict(summary)
    for key in [k for k in s if k.startswith("_")]:
        del s[key]
    return s


def create_app(
    calls_dir: Path,
    recordings_dir: Path,
    db_path: Path,
    *,
    allow_testserver: bool = False,
) -> FastAPI:
    app = FastAPI(title="Clinic Console API", docs_url=None, redoc_url=None)

    hosts = ["127.0.0.1", "localhost"]
    if allow_testserver:
        hosts.append("testserver")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(CORS_ORIGINS),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    def _require_db() -> sqlite3.Connection:
        try:
            return connect_db_ro(db_path)
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="diary database not available") from None

    def _summaries_for_dir() -> list[dict[str, Any]]:
        same_day = _load_same_day_urgent(db_path)
        items: list[dict[str, Any]] = []
        for path in list_call_files(calls_dir):
            call_id = path.name[:-6]
            try:
                mtime = path.stat().st_mtime
                events, bad = parse_jsonl(path)
            except OSError:
                continue  # removed (e.g. purged) between listing and reading
            summary = build_summary(call_id, events, bad, recordings_dir, same_day, mtime)
            summary["_metrics"] = _last_audio_metrics(events)
            items.append(summary)
        items.sort(key=lambda s: s["_sort_key"], reverse=True)
        return items

    @app.get("/api/calls")
    def list_calls(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        flag: Annotated[list[str] | None, Query()] = None,
        view: str | None = None,
    ) -> dict[str, Any]:
        flag_vals = flag or []
        for f in flag_vals:
            if f not in OUTCOME_FLAGS:
                raise HTTPException(status_code=422, detail=f"unknown flag: {f}")

        active_flags: list[str] = list(flag_vals)
        if view == "quota":
            active_flags.append("provider_quota")
        elif view == "staff_today":
            active_flags.extend(["same_day_urgent", "urgent_callback"])
        elif view is not None:
            raise HTTPException(status_code=422, detail="unknown view")

        summaries = _summaries_for_dir()
        if active_flags:
            flag_set = set(active_flags)

            def matches(s: dict[str, Any]) -> bool:
                call_flags = set(s.get("flags") or [])
                return bool(call_flags & flag_set)

            summaries = [s for s in summaries if matches(s)]

        total = len(summaries)
        page = summaries[offset : offset + limit]
        return {
            "total": total,
            "calls": [_strip_internal(s) for s in page],
        }

    def _validate_call_id(call_id: str) -> None:
        if not CALL_ID_RE.fullmatch(call_id):
            raise HTTPException(status_code=404, detail="call not found")

    @app.get("/api/calls/{call_id}")
    def get_call(call_id: str) -> dict[str, Any]:
        _validate_call_id(call_id)
        path = _safe_child_file(calls_dir, call_id, ".jsonl")
        if path is None:
            raise HTTPException(status_code=404, detail="call not found")
        try:
            mtime = path.stat().st_mtime
            events, bad = parse_jsonl(path)
        except OSError:
            raise HTTPException(status_code=404, detail="call not found") from None
        same_day = _load_same_day_urgent(db_path)
        summary = build_summary(call_id, events, bad, recordings_dir, same_day, mtime)
        metrics_ev = _last_audio_metrics(events)
        latency = build_latency_block(metrics_ev) if metrics_ev else None
        timing_path = _safe_child_file(recordings_dir, call_id, ".timing.json")
        timing = None
        if timing_path is not None:
            timing = _read_timing(timing_path)
        db_block = None
        try:
            with closing(connect_db_ro(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                changes = [
                    {
                        **dict(row),
                        "details": json.loads(row["details"]),
                    }
                    for row in conn.execute("SELECT * FROM changes WHERE call_id = ?", (call_id,))
                ]
                messages = [
                    dict(row)
                    for row in conn.execute("SELECT * FROM messages WHERE call_id = ?", (call_id,))
                ]
                appointments = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM appointments WHERE created_by_call = ?", (call_id,)
                    )
                ]
                db_block = {
                    "changes": changes,
                    "messages": messages,
                    "appointments": appointments,
                }
        except FileNotFoundError:
            db_block = None

        return {
            "summary": _strip_internal(summary),
            "events": events,
            "audio_metrics": metrics_ev,
            "latency": latency,
            "timing": timing,
            "db": db_block,
        }

    @app.get("/api/calls/{call_id}/audio")
    def get_audio(call_id: str) -> FileResponse:
        _validate_call_id(call_id)
        wav = _safe_child_file(recordings_dir, call_id, ".wav")
        if wav is None:
            raise HTTPException(status_code=404, detail="audio not found")
        return FileResponse(
            wav,
            media_type="audio/wav",
            filename=f"{call_id}.wav",
            content_disposition_type="inline",
        )

    @app.get("/api/calls/{call_id}/timing")
    def get_timing(call_id: str) -> dict[str, Any]:
        _validate_call_id(call_id)
        timing_path = _safe_child_file(recordings_dir, call_id, ".timing.json")
        if timing_path is None:
            raise HTTPException(status_code=404, detail="call not found")
        timing = _read_timing(timing_path)
        if timing is None:
            raise HTTPException(status_code=404, detail="call not found")
        return timing

    @app.get("/api/appointments")
    def appointments_by_date(
        date_str: Annotated[str, Query(alias="date")],
    ) -> dict[str, Any]:
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid date") from None
        date_str = day.isoformat()

        with closing(_require_db()) as conn:
            conn.row_factory = sqlite3.Row
            site = conn.execute("SELECT timezone FROM site LIMIT 1").fetchone()
            if site is None:
                raise HTTPException(status_code=503, detail="diary database not available")
            tz = ZoneInfo(site["timezone"])
            start_local = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
            end_local = start_local + timedelta(days=1)
            start_utc = start_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc = end_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

            closure = conn.execute(
                "SELECT name FROM closures WHERE date = ?", (date_str,)
            ).fetchone()
            absences = [
                {"practitioner_id": r["practitioner_id"], "reason": r["reason"]}
                for r in conn.execute(
                    "SELECT practitioner_id, reason FROM absences "
                    "WHERE start_date <= ? AND end_date >= ?",
                    (date_str, date_str),
                )
            ]
            practitioners = [
                {"practitioner_id": r["practitioner_id"], "name": r["name"], "role": r["role"]}
                for r in conn.execute(
                    "SELECT practitioner_id, name, role FROM practitioners ORDER BY name"
                )
            ]
            rows = conn.execute(
                """
                SELECT a.*, p.first_name, p.last_name1, p.last_name2,
                       pr.name AS practitioner_name,
                       t.name_es AS treatment_name_es, t.name_en AS treatment_name_en
                FROM appointments a
                JOIN patients p ON p.patient_id = a.patient_id
                JOIN practitioners pr ON pr.practitioner_id = a.practitioner_id
                JOIN treatments t ON t.treatment_id = a.treatment_id
                WHERE a.start_utc >= ? AND a.start_utc < ?
                ORDER BY a.start_utc, a.practitioner_id
                """,
                (start_utc, end_utc),
            ).fetchall()

        appts: list[dict[str, Any]] = []
        for r in rows:
            st_utc = datetime.strptime(r["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            en_utc = datetime.strptime(r["end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            parts = [r["first_name"], r["last_name1"]]
            if r["last_name2"]:
                parts.append(r["last_name2"])
            appts.append(
                {
                    "appointment_id": r["appointment_id"],
                    "status": r["status"],
                    "start_utc": r["start_utc"],
                    "end_utc": r["end_utc"],
                    "start_local": st_utc.astimezone(tz).isoformat(),
                    "end_local": en_utc.astimezone(tz).isoformat(),
                    "practitioner_id": r["practitioner_id"],
                    "practitioner_name": r["practitioner_name"],
                    "treatment_id": r["treatment_id"],
                    "treatment_name_es": r["treatment_name_es"],
                    "treatment_name_en": r["treatment_name_en"],
                    "patient_id": r["patient_id"],
                    "patient_name": " ".join(parts),
                    "same_day_urgent": bool(r["same_day_urgent"]),
                    "created_by_call": r["created_by_call"],
                }
            )

        return {
            "date": date_str,
            "timezone": site["timezone"],
            "closure": closure["name"] if closure else None,
            "absences": absences,
            "practitioners": practitioners,
            "appointments": appts,
        }

    @app.get("/api/overview")
    def overview(
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
    ) -> dict[str, Any]:
        if from_date is not None:
            try:
                date.fromisoformat(from_date)
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid from") from None
        if to_date is not None:
            try:
                date.fromisoformat(to_date)
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid to") from None

        summaries = _summaries_for_dir()
        if from_date or to_date:

            def in_range(s: dict[str, Any]) -> bool:
                started = s.get("started_at")
                if not started:
                    return False
                day = started[:10]
                if from_date and day < from_date:
                    return False
                return not (to_date and day > to_date)

            summaries = [s for s in summaries if in_range(s)]

        by_result = {"changed": 0, "no_action": 0, "unknown": 0}
        by_no_action: dict[str, int] = {}
        by_flag = {f: 0 for f in sorted(OUTCOME_FLAGS)}
        by_channel: dict[str, int] = {}
        by_language: dict[str, int] = {}
        in_progress = 0
        with_audio = 0

        pooled_answered = 0
        pooled_overlapped = 0
        pooled_unanswered = 0
        per_call_p50: list[float] = []
        n_calls_metrics = 0
        audio_sums = {
            "dead_air_secs": 0.0,
            "barge_ins": 0,
            "underruns": 0,
            "echo_transcripts": 0,
            "transcripts_missing": 0,
            "guard_firings": 0,
            "skipped_turns": 0,
        }

        for s in summaries:
            if s.get("in_progress"):
                in_progress += 1
            out = s.get("outcome")
            if out and out.get("result") == "changed":
                by_result["changed"] += 1
            elif out and out.get("result") == "no_action":
                by_result["no_action"] += 1
                reason = out.get("no_action_reason")
                if isinstance(reason, str):
                    by_no_action[reason] = by_no_action.get(reason, 0) + 1
            else:
                by_result["unknown"] += 1
            for f in s.get("flags") or []:
                if f in by_flag:
                    by_flag[f] += 1
            ch = s.get("channel")
            if isinstance(ch, str):
                by_channel[ch] = by_channel.get(ch, 0) + 1
            lang = s.get("language")
            if isinstance(lang, str):
                by_language[lang] = by_language.get(lang, 0) + 1
            if s.get("audio", {}).get("available"):
                with_audio += 1

            metrics = s.get("_metrics")
            if metrics:
                n_calls_metrics += 1
                answered = _count(metrics.get("answered_turns"))
                overlapped = _count(metrics.get("overlapped_turns"))
                turns = _count(metrics.get("turns"))
                pooled_answered += answered
                pooled_overlapped += overlapped
                pooled_unanswered += max(turns - answered - overlapped, 0)
                p50 = _num(metrics.get("latency_p50_ms"))
                if answered > 0 and p50 is not None:
                    per_call_p50.append(p50)
                for key in audio_sums:
                    if key == "dead_air_secs":
                        audio_sums[key] += _num(metrics.get(key)) or 0.0
                    else:
                        audio_sums[key] += _count(metrics.get(key))

        pool_rate = _overlap_rate(pooled_answered, pooled_overlapped)
        call_p50 = linear_percentile(per_call_p50, 50)
        call_p90 = linear_percentile(per_call_p50, 90)
        if n_calls_metrics == 0:
            latency_block = {
                "which": "server",
                "calls": 0,
                "answered": 0,
                "overlapped": 0,
                "unanswered": 0,
                "overlap_rate": None,
                "call_p50_ms": {"p50": None, "p90": None, "n_calls": 0},
                "text": "no latency data",
            }
        else:
            latency_block = {
                "which": "server",
                "calls": n_calls_metrics,
                "answered": pooled_answered,
                "overlapped": pooled_overlapped,
                "unanswered": pooled_unanswered,
                "overlap_rate": pool_rate,
                "call_p50_ms": {
                    "p50": call_p50,
                    "p90": call_p90,
                    "n_calls": len(per_call_p50),
                },
                "text": latency_text(
                    call_p50,
                    call_p90,
                    pooled_answered,
                    pooled_overlapped,
                    pooled_unanswered,
                    prefix="per-call p50: ",
                    suffix=f"; {n_calls_metrics} calls",
                ),
            }

        diary = None
        try:
            with closing(connect_db_ro(db_path)) as conn:
                diary = {
                    "messages_urgent": conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE urgent = 1"
                    ).fetchone()[0],
                    "messages_total": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    "appointments_booked": conn.execute(
                        "SELECT COUNT(*) FROM appointments WHERE status = 'booked'"
                    ).fetchone()[0],
                }
        except FileNotFoundError:
            diary = None

        return {
            "calls": {
                "total": len(summaries),
                "in_progress": in_progress,
                "by_result": by_result,
                "by_no_action_reason": by_no_action,
                "by_flag": by_flag,
                "by_channel": by_channel,
                "by_language": by_language,
                "with_audio": with_audio,
            },
            "latency": latency_block,
            "audio": audio_sums,
            "diary": diary,
        }

    return app


def _env_paths() -> tuple[Path, Path, Path]:
    calls_dir = Path(os.getenv("CALLS_DIR", str(config.CALLS_DIR)))
    recordings_dir = Path(os.getenv("RECORDINGS_DIR", str(config.ROOT / "recordings")))
    db_path = Path(os.getenv("CLINIC_DB", str(config.ROOT / "data" / "clinic.db")))
    return calls_dir, recordings_dir, db_path


_calls_dir, _recordings_dir, _db_path = _env_paths()
app = create_app(_calls_dir, _recordings_dir, _db_path)


def main() -> None:
    port = int(os.getenv("CONSOLE_PORT", "7861"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
