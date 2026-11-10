"""Build the clinic SQLite database from the static catalogue."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from seed import catalogue, people
from seed.diary import generate_appointments
from seed.timeutil import parse_local_date
from seed.validate import occupancy, validate

SEED_VERSION = "1"
DEFAULT_ANCHOR = "2026-11-16"
DEFAULT_SEED = 20261116

_BACKEND = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent
DEFAULT_DB = _REPO_ROOT / "data" / "clinic.db"
_SCHEMA = _BACKEND / "engine" / "schema.sql"


def _monday_anchor(anchor_text: str) -> date:
    d = parse_local_date(anchor_text)
    if d.weekday() != 0:
        msg = f"anchor must be a Monday, got {anchor_text} (weekday {d.weekday()})"
        raise SystemExit(msg)
    return d


def _remove_db_files(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{db_path}{suffix}")
        if p.exists():
            p.unlink()


def build_db(db_path: Path, anchor: date, seed: int) -> list[str]:
    _remove_db_files(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_sql = _SCHEMA.read_text(encoding="utf-8")
    patients, people_fixtures = people.generate_patients(seed)
    patient_ids = [p["patient_id"] for p in patients]
    appointments, diary_fixtures = generate_appointments(anchor, seed, patient_ids)

    fixtures = dict(people_fixtures)
    fixtures.update(diary_fixtures)
    fixtures_json = json.dumps(fixtures, sort_keys=True, separators=(",", ":"))

    diary_start = anchor.isoformat()
    diary_end = (anchor + timedelta(days=27)).isoformat()
    absences = catalogue.absences_for_anchor(anchor)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.execute("BEGIN")
        meta_rows = (
            ("seed_version", SEED_VERSION),
            ("seed_anchor", anchor.isoformat()),
            ("seed_rng", str(seed)),
            ("diary_start", diary_start),
            ("diary_end", diary_end),
            ("fixtures", fixtures_json),
        )
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta_rows)

        s = catalogue.SITE
        conn.execute(
            "INSERT INTO site (site_id, name, address, phone, timezone) VALUES (?, ?, ?, ?, ?)",
            (s["site_id"], s["name"], s["address"], s["phone"], s["timezone"]),
        )

        for p in catalogue.PRACTITIONERS:
            conn.execute(
                "INSERT INTO practitioners (practitioner_id, name, role, languages)"
                " VALUES (?, ?, ?, ?)",
                (p["practitioner_id"], p["name"], p["role"], p["languages"]),
            )

        for t in catalogue.TREATMENTS:
            conn.execute(
                "INSERT INTO treatments (treatment_id, name_es, name_en, minutes, price_eur)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    t["treatment_id"],
                    t["name_es"],
                    t["name_en"],
                    t["minutes"],
                    t["price_eur"],
                ),
            )

        conn.executemany(
            "INSERT INTO practitioner_treatments (practitioner_id, treatment_id) VALUES (?, ?)",
            catalogue.PRACTITIONER_TREATMENTS,
        )

        conn.executemany(
            "INSERT INTO working_hours (practitioner_id, weekday, start_local, end_local)"
            " VALUES (?, ?, ?, ?)",
            catalogue.WORKING_HOURS,
        )

        conn.executemany("INSERT INTO closures (date, name) VALUES (?, ?)", catalogue.CLOSURES)

        conn.executemany(
            "INSERT INTO absences (practitioner_id, start_date, end_date, reason)"
            " VALUES (?, ?, ?, ?)",
            ((a["practitioner_id"], a["start_date"], a["end_date"], a["reason"]) for a in absences),
        )

        conn.executemany(
            "INSERT INTO patients (patient_id, first_name, last_name1, last_name2, dob,"
            " phone, language, created_by_call) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    p["patient_id"],
                    p["first_name"],
                    p["last_name1"],
                    p["last_name2"],
                    p["dob"],
                    p["phone"],
                    p["language"],
                    p["created_by_call"],
                )
                for p in patients
            ),
        )

        conn.executemany(
            "INSERT INTO appointments (appointment_id, patient_id, practitioner_id,"
            " treatment_id, start_utc, end_utc, status, same_day_urgent, created_by_call)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    a["appointment_id"],
                    a["patient_id"],
                    a["practitioner_id"],
                    a["treatment_id"],
                    a["start_utc"],
                    a["end_utc"],
                    a["status"],
                    a["same_day_urgent"],
                    a["created_by_call"],
                )
                for a in appointments
            ),
        )

        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()

    return validate(db_path)


def _print_stats(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            "meta",
            "site",
            "practitioners",
            "treatments",
            "practitioner_treatments",
            "working_hours",
            "closures",
            "absences",
            "patients",
            "appointments",
            "changes",
            "messages",
            "review_links",
        ]
        print("Table counts:")
        for t in tables:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")

        print("Occupancy % (booked minutes / working minutes):")
        occ = occupancy(db_path)
        for pid in sorted(occ):
            b = occ[pid]
            print(
                f"  {pid}: history={b['history'] * 100:.1f}%"
                f" week1={b['week1'] * 100:.1f}%"
                f" week2={b['week2'] * 100:.1f}%"
                f" week3={b['week3'] * 100:.1f}%"
                f" week4={b['week4'] * 100:.1f}%"
            )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build clinic database")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--anchor", default=DEFAULT_ANCHOR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    anchor = _monday_anchor(args.anchor)
    errors = build_db(args.db.resolve(), anchor, args.seed)
    _print_stats(args.db.resolve())
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  {e}")
        raise SystemExit(1)
    print("Seed complete.")


if __name__ == "__main__":
    main()
