"""Fixture builders for console API tests."""

from __future__ import annotations

import json
import sqlite3
import struct
import wave
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "engine" / "schema.sql"

SITE_ID = "site-nova"
TZ = "Europe/Madrid"


def write_call(calls_dir: Path, call_id: str, events: list[dict]) -> None:
    calls_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, separators=(",", ":")) for e in events]
    (calls_dir / f"{call_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_truncated_call(calls_dir: Path, call_id: str, events: list[dict], fragment: str) -> None:
    calls_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, separators=(",", ":")) for e in events]
    (calls_dir / f"{call_id}.jsonl").write_text(
        "\n".join(lines) + "\n" + fragment, encoding="utf-8"
    )


def write_wav(rec_dir: Path, call_id: str, secs: float) -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    rate = 24000
    nframes = int(rate * secs)
    path = rec_dir / f"{call_id}.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * (nframes * 2))
    return path


def write_corrupt_wav_header(rec_dir: Path, call_id: str) -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / f"{call_id}.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", 8) + b"WAVE")
    return path


def write_timing(rec_dir: Path, call_id: str, data: dict | None = None) -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    payload = data or {
        "rate": 24000,
        "caller": [[0.0, 1.5]],
        "agent": [[2.0, 4.0]],
        "underruns": [],
    }
    path = rec_dir / f"{call_id}.timing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO site (site_id, name, address, phone, timezone) VALUES (?,?,?,?,?)",
            (SITE_ID, "Clínica Nova", "Calle Lirio 12", "+34910001122", TZ),
        )
        conn.execute(
            "INSERT INTO practitioners (practitioner_id, name, role, languages) VALUES (?,?,?,?)",
            ("prac-elena", "Dra. Elena Navas", "dentist", "es,en"),
        )
        conn.execute(
            "INSERT INTO practitioners (practitioner_id, name, role, languages) VALUES (?,?,?,?)",
            ("prac-sofia", "Sofia Ortega", "hygienist", "es"),
        )
        conn.execute(
            "INSERT INTO treatments (treatment_id, name_es, name_en, minutes, price_eur) "
            "VALUES (?,?,?,?,?)",
            ("tx-clean", "Limpieza", "Cleaning", 30, 45),
        )
        conn.execute(
            "INSERT INTO treatments (treatment_id, name_es, name_en, minutes, price_eur) "
            "VALUES (?,?,?,?,?)",
            ("tx-check", "Revisión", "Check-up", 15, 30),
        )
        for pid, first, last1, last2, phone in (
            ("pat-001", "Luis", "Marín", None, "+34600111222"),
            ("pat-002", "Inés", "Vega", "López", "+34600333444"),
            ("pat-003", "Tomás", "Rey", None, "+34600555666"),
        ):
            conn.execute(
                "INSERT INTO patients (patient_id, first_name, last_name1, last_name2, dob, phone) "
                "VALUES (?,?,?,?,?,?)",
                (pid, first, last1, last2, "1990-05-10", phone),
            )
        appt_cols = (
            "appointment_id, patient_id, practitioner_id, treatment_id, "
            "start_utc, end_utc, status, same_day_urgent, created_by_call"
        )
        conn.execute(
            f"INSERT INTO appointments ({appt_cols}) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "appt-booked",
                "pat-001",
                "prac-elena",
                "tx-check",
                "2026-10-25T08:00:00Z",
                "2026-10-25T08:15:00Z",
                "booked",
                0,
                "aabbccddeeff",
            ),
        )
        conn.execute(
            f"INSERT INTO appointments ({appt_cols}) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "appt-urgent",
                "pat-002",
                "prac-elena",
                "tx-clean",
                "2026-10-25T14:00:00Z",
                "2026-10-25T14:30:00Z",
                "booked",
                1,
                "112233445566",
            ),
        )
        conn.execute(
            f"INSERT INTO appointments ({appt_cols}) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "appt-cancel",
                "pat-003",
                "prac-sofia",
                "tx-clean",
                "2026-10-26T09:00:00Z",
                "2026-10-26T09:30:00Z",
                "cancelled",
                0,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO closures (date, name) VALUES (?,?)",
            ("2026-12-24", "Nochebuena"),
        )
        conn.execute(
            "INSERT INTO absences (practitioner_id, start_date, end_date, reason) VALUES (?,?,?,?)",
            ("prac-sofia", "2026-10-25", "2026-10-25", "Formación"),
        )
        conn.execute(
            "INSERT INTO messages (message_id, call_id, name, phone, text, urgent, saved_utc) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "msg-1",
                "aabbccddeeff",
                "Pilar",
                "+34600777888",
                "Llame cuando pueda",
                0,
                "2026-10-01T10:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO messages (message_id, call_id, name, phone, text, urgent, saved_utc) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "msg-2",
                "998877665544",
                "Héctor",
                "+34600999000",
                "Dolor fuerte",
                1,
                "2026-10-02T11:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO changes (operation_key, call_id, action, details, saved_utc) "
            "VALUES (?,?,?,?,?)",
            (
                "op-aabb",
                "aabbccddeeff",
                "book",
                json.dumps({"appointment_id": "appt-booked"}),
                "2026-10-25T07:55:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def complete_call_events(call_id: str = "aabbccddeeff") -> list[dict]:
    return [
        {
            "t": 0.0,
            "kind": "call_started",
            "schema": 1,
            "now": "2026-10-25T09:00:00Z",
            "monotonic": 1000.0,
            "voice": "kokoro",
            "channel": "phone",
            "language": "es",
        },
        {"t": 1.0, "kind": "transcript", "role": "caller", "text": "Hola"},
        {"t": 2.0, "kind": "transcript", "role": "agent", "text": "Buenos días"},
        {
            "t": 30.0,
            "kind": "audio_metrics",
            "greeting_secs": 1.2,
            "turns": 10,
            "answered_turns": 8,
            "overlapped_turns": 2,
            "overlap_rate": 0.2,
            "latency_p50_ms": 1200,
            "latency_p90_ms": 2100,
            "skipped_turns": 0,
            "guard_firings": 1,
            "dead_air_secs": 0.5,
            "barge_ins": 1,
            "yield_p50_ms": 100,
            "underruns": 0,
            "echo_transcripts": 0,
            "transcripts_missing": 0,
        },
        {
            "t": 31.0,
            "kind": "outcome",
            "result": "changed",
            "changes": ["appt-booked"],
            "escalations": [],
            "flags": [],
            "derived": True,
        },
        {"t": 32.0, "kind": "call_ended", "duration_secs": 32.0},
        {"t": 33.0, "kind": "latency", "heard_latency_ms": 900},
    ]
