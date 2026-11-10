"""Validate seeded SQLite database (reads only the db file plus phones.is_fictional)."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from seed import phones
from seed.timeutil import (
    MADRID,
    parse_local_date,
    quarter_hours_between,
    utc_range_ok,
    weekday_local,
)

REQUIRED_META = frozenset(
    {
        "diary_end",
        "diary_start",
        "fixtures",
        "seed_anchor",
        "seed_rng",
        "seed_version",
    }
)

CLOSED_TREATMENTS = frozenset(
    {
        "checkup",
        "cleaning",
        "extraction",
        "filling",
        "ortho_consult",
        "urgency",
    }
)

UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def _parse_fixtures(raw: str | None) -> tuple[dict, str | None]:
    if raw is None:
        return {}, "meta fixtures missing"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}, "meta fixtures: invalid JSON"
    if not isinstance(data, dict):
        return {}, "meta fixtures: expected JSON object"
    return data, None


def _parse_meta_date(meta: dict[str, str], key: str) -> tuple[date | None, str | None]:
    if key not in meta:
        return None, f"meta missing key: {key}"
    try:
        return parse_local_date(meta[key]), None
    except (ValueError, TypeError):
        return None, f"meta {key} unparsable: {meta[key]!r}"


def _practitioner_on_absence(
    pid: str, d: date, absence: dict[str, list[tuple[date, date]]]
) -> bool:
    return any(start <= d <= end for start, end in absence.get(pid, []))


def _working_minutes_day(
    pid: str,
    d: date,
    hours: dict[str, dict[int, list[tuple[str, str]]]],
    closures: set[str],
    absence: dict[str, list[tuple[date, date]]],
) -> int:
    iso = d.isoformat()
    if iso in closures:
        return 0
    if _practitioner_on_absence(pid, d, absence):
        return 0
    wd = weekday_local(d)
    total = 0
    for seg_start, seg_end in hours[pid].get(wd, []):
        total += len(quarter_hours_between(seg_start, seg_end)) * 15
    return total


def occupancy(db_path: str | Path) -> dict[str, dict[str, float]]:
    """Booked minutes / working minutes per practitioner for history and future weeks."""
    path = Path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        meta = _load_meta(conn)
        anchor, err = _parse_meta_date(meta, "diary_start")
        if anchor is None:
            if err:
                raise ValueError(err)
            raise ValueError("diary_start missing")

        history_start = anchor - timedelta(days=14)

        hours: dict[str, dict[int, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
        for r in conn.execute(
            "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
        ):
            hours[r["practitioner_id"]][r["weekday"]].append((r["start_local"], r["end_local"]))

        closures = {r["date"] for r in conn.execute("SELECT date FROM closures")}
        absence: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for r in conn.execute("SELECT practitioner_id, start_date, end_date FROM absences"):
            absence[r["practitioner_id"]].append(
                (parse_local_date(r["start_date"]), parse_local_date(r["end_date"]))
            )

        practitioners = [
            r["practitioner_id"]
            for r in conn.execute(
                "SELECT practitioner_id FROM practitioners ORDER BY practitioner_id"
            )
        ]

        tmap = {
            r["treatment_id"]: r["minutes"]
            for r in conn.execute("SELECT treatment_id, minutes FROM treatments")
        }

        week_ranges = {
            "history": (history_start, anchor - timedelta(days=1)),
            "week1": (anchor, anchor + timedelta(days=6)),
            "week2": (anchor + timedelta(days=7), anchor + timedelta(days=13)),
            "week3": (anchor + timedelta(days=14), anchor + timedelta(days=20)),
            "week4": (anchor + timedelta(days=21), anchor + timedelta(days=27)),
        }

        work: dict[str, dict[str, int]] = {
            pid: {k: 0 for k in week_ranges} for pid in practitioners
        }
        booked: dict[str, dict[str, int]] = {
            pid: {k: 0 for k in week_ranges} for pid in practitioners
        }

        for pid in practitioners:
            for label, (start_d, end_d) in week_ranges.items():
                d = start_d
                while d <= end_d:
                    work[pid][label] += _working_minutes_day(pid, d, hours, closures, absence)
                    d += timedelta(days=1)

        for row in conn.execute(
            "SELECT practitioner_id, treatment_id, start_utc, status FROM appointments"
        ):
            if row["status"] != "booked":
                continue
            start_dt = datetime.strptime(row["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            loc_d = start_dt.astimezone(MADRID).date()
            mins = int(tmap[row["treatment_id"]])
            pid = row["practitioner_id"]
            for label, (start_d, end_d) in week_ranges.items():
                if start_d <= loc_d <= end_d:
                    booked[pid][label] += mins
                    break

        out: dict[str, dict[str, float]] = {}
        for pid in practitioners:
            out[pid] = {}
            for label in week_ranges:
                w = work[pid][label]
                out[pid][label] = (booked[pid][label] / w) if w else 0.0
        return out
    finally:
        conn.close()


def validate(db_path: str | Path) -> list[str]:
    errors: list[str] = []
    path = Path(db_path)
    if not path.is_file():
        return [f"database file missing: {path}"]

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    diary_start: date | None = None
    diary_end: date | None = None
    fixtures: dict = {}
    fixtures_ok = True
    diary_bounds_ok = False
    try:
        meta = _load_meta(conn)
        for key in sorted(REQUIRED_META):
            if key not in meta:
                errors.append(f"meta missing key: {key}")

        if meta.get("seed_version") != "1":
            errors.append(f"unexpected seed_version: {meta.get('seed_version')!r}")

        if "seed_anchor" in meta:
            _, anchor_err = _parse_meta_date(meta, "seed_anchor")
            if anchor_err and anchor_err.startswith("meta seed_anchor unparsable"):
                errors.append(anchor_err)

        fixtures, fix_err = _parse_fixtures(meta.get("fixtures"))
        if fix_err:
            errors.append(fix_err)
            fixtures_ok = False
        elif "fixtures" in meta:
            for fix_key in ("namesakes", "same_first_surname", "accented", "fully_booked"):
                if fix_key not in fixtures:
                    errors.append(f"fixtures missing key: {fix_key}")

        diary_start, ds_err = _parse_meta_date(meta, "diary_start")
        if ds_err:
            errors.append(ds_err)
        diary_end, de_err = _parse_meta_date(meta, "diary_end")
        if de_err:
            errors.append(de_err)
        diary_bounds_ok = diary_start is not None and diary_end is not None

        practitioners = conn.execute(
            "SELECT * FROM practitioners ORDER BY practitioner_id"
        ).fetchall()
        dentists = [r for r in practitioners if r["role"] == "dentist"]
        hygienists = [r for r in practitioners if r["role"] == "hygienist"]
        if len(dentists) != 4:
            errors.append(f"expected 4 dentists, found {len(dentists)}")
        if len(hygienists) != 1:
            errors.append(f"expected 1 hygienist, found {len(hygienists)}")

        treatment_rows = conn.execute("SELECT * FROM treatments ORDER BY treatment_id").fetchall()
        found_tids = {r["treatment_id"] for r in treatment_rows}
        if found_tids != CLOSED_TREATMENTS:
            errors.append(f"treatment ids mismatch: {sorted(found_tids)}")

        for row in treatment_rows:
            mins = row["minutes"]
            if mins % 15 != 0 or mins <= 0:
                errors.append(f"treatment {row['treatment_id']}: invalid minutes {mins}")
            if row["treatment_id"] == "ortho_consult" and row["price_eur"] is not None:
                errors.append("ortho_consult price must be NULL")

        pmap = {r["practitioner_id"]: dict(r) for r in practitioners}
        tmap = {r["treatment_id"]: dict(r) for r in treatment_rows}
        ptreat = defaultdict(set)
        for r in conn.execute("SELECT practitioner_id, treatment_id FROM practitioner_treatments"):
            ptreat[r["practitioner_id"]].add(r["treatment_id"])

        hours: dict[str, dict[int, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
        for r in conn.execute(
            "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
        ):
            hours[r["practitioner_id"]][r["weekday"]].append((r["start_local"], r["end_local"]))

        closures = {r["date"] for r in conn.execute("SELECT date FROM closures")}

        absence: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for r in conn.execute(
            "SELECT practitioner_id, start_date, end_date FROM absences ORDER BY practitioner_id"
        ):
            absence[r["practitioner_id"]].append(
                (parse_local_date(r["start_date"]), parse_local_date(r["end_date"]))
            )

        patient_count = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        if patient_count != 300:
            errors.append(f"expected 300 patients, found {patient_count}")

        patients = {r["patient_id"]: dict(r) for r in conn.execute("SELECT * FROM patients")}
        for row in patients.values():
            if not phones.is_fictional(row["phone"]):
                errors.append(f"patient {row['patient_id']}: non-fictional phone")

        site_rows = conn.execute("SELECT site_id, phone, timezone FROM site").fetchall()
        if len(site_rows) != 1:
            errors.append(f"expected exactly 1 site row, found {len(site_rows)}")
        else:
            site = site_rows[0]
            if site["timezone"] != "Europe/Madrid":
                errors.append(f"site timezone must be Europe/Madrid, got {site['timezone']!r}")
            if not phones.is_fictional(site["phone"]):
                errors.append("site phone is not fictional")

        if fixtures_ok:
            ns_ids = fixtures.get("namesakes", [])
            if len(ns_ids) == 2:
                a, b = (patients.get(ns_ids[0]), patients.get(ns_ids[1]))
                if a and b:
                    if not (
                        a["first_name"] == b["first_name"]
                        and a["last_name1"] == b["last_name1"]
                        and a["last_name2"] == b["last_name2"]
                        and a["dob"] != b["dob"]
                    ):
                        errors.append("namesake fixtures do not match expected shape")
                else:
                    errors.append("namesake fixture patients missing")
            else:
                errors.append("namesakes fixture must list two patient ids")

            sfs_ids = fixtures.get("same_first_surname", [])
            if len(sfs_ids) == 2:
                a, b = (patients.get(sfs_ids[0]), patients.get(sfs_ids[1]))
                if a and b:
                    if not (
                        a["first_name"] == b["first_name"]
                        and a["last_name1"] == b["last_name1"]
                        and a["last_name2"] != b["last_name2"]
                    ):
                        errors.append("same_first_surname fixtures invalid")
                else:
                    errors.append("same_first_surname patients missing")

            acc_id = fixtures.get("accented")
            if acc_id and acc_id not in patients:
                errors.append("accented fixture patient missing")

        booked = conn.execute(
            "SELECT * FROM appointments WHERE status = 'booked' ORDER BY start_utc"
        ).fetchall()
        all_appts = conn.execute("SELECT * FROM appointments ORDER BY start_utc").fetchall()

        pr_booked: dict[str, list[tuple[str, str]]] = defaultdict(list)
        pt_booked: dict[str, list[tuple[str, str]]] = defaultdict(list)

        def overlaps(iv: list[tuple[str, str]], s: str, e: str) -> bool:
            return any(a < e and b > s for a, b in iv)

        for row in all_appts:
            aid = row["appointment_id"]
            pid = row["practitioner_id"]
            pat = row["patient_id"]
            tid = row["treatment_id"]
            start_u = row["start_utc"]
            end_u = row["end_utc"]

            if not UTC_PATTERN.match(start_u) or not UTC_PATTERN.match(end_u):
                errors.append(f"{aid}: malformed UTC timestamp")
            if not utc_range_ok(start_u, end_u):
                errors.append(f"{aid}: invalid UTC range")

            if pid not in pmap:
                errors.append(f"{aid}: unknown practitioner")
                continue
            if tid not in CLOSED_TREATMENTS:
                errors.append(f"{aid}: unknown treatment")
            if tid not in ptreat.get(pid, set()):
                errors.append(f"{aid}: practitioner cannot perform {tid}")

            try:
                start_dt = datetime.strptime(start_u, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                end_dt = datetime.strptime(end_u, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except ValueError:
                errors.append(f"{aid}: UTC parse failed")
                continue

            loc_start = start_dt.astimezone(MADRID)
            loc_end = end_dt.astimezone(MADRID)
            d = loc_start.date()
            wd = weekday_local(d)
            hhmm = f"{loc_start.hour:02d}:{loc_start.minute:02d}"
            if loc_start.minute % 15 != 0 or loc_start.second != 0:
                errors.append(f"{aid}: not on 15-minute grid locally")

            expected_mins = tmap[tid]["minutes"]
            actual_mins = int((end_dt - start_dt).total_seconds() // 60)
            if actual_mins != expected_mins:
                errors.append(f"{aid}: duration {actual_mins} != treatment {expected_mins}")

            end_hhmm = f"{loc_end.hour:02d}:{loc_end.minute:02d}"
            fits = False
            for seg_start, seg_end in hours[pid].get(wd, []):
                if seg_start <= hhmm and end_hhmm <= seg_end:
                    fits = True
                    break
            if not fits:
                errors.append(f"{aid}: outside working hours")

            iso = d.isoformat()
            if iso in closures:
                errors.append(f"{aid}: on closure {iso}")
            for abs_start, abs_end in absence.get(pid, []):
                if abs_start <= d <= abs_end:
                    errors.append(f"{aid}: during absence for {pid}")

            if row["status"] == "booked":
                if overlaps(pr_booked[pid], start_u, end_u):
                    errors.append(f"{aid}: practitioner overlap")
                if overlaps(pt_booked[pat], start_u, end_u):
                    errors.append(f"{aid}: patient overlap")
                pr_booked[pid].append((start_u, end_u))
                pt_booked[pat].append((start_u, end_u))

        if fixtures_ok:
            fb = fixtures.get("fully_booked", {})
            fb_pid = fb.get("practitioner_id")
            fb_date = fb.get("date")
            if fb_pid and fb_date:
                d = parse_local_date(fb_date)
                if fb_date in closures:
                    errors.append("fully_booked day is a closure")
                day_slots = []
                for seg_start, seg_end in hours[fb_pid].get(weekday_local(d), []):
                    day_slots.extend(quarter_hours_between(seg_start, seg_end))
                busy_cells: set[str] = set()
                for row in booked:
                    if row["practitioner_id"] != fb_pid:
                        continue
                    start_dt = datetime.strptime(row["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=UTC
                    )
                    end_dt = datetime.strptime(row["end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=UTC
                    )
                    loc_s = start_dt.astimezone(MADRID)
                    loc_e = end_dt.astimezone(MADRID)
                    if loc_s.date() != d:
                        continue
                    h0, m0 = loc_s.hour, loc_s.minute
                    h1, m1 = loc_e.hour, loc_e.minute
                    t0 = h0 * 60 + m0
                    t1 = h1 * 60 + m1
                    for t in range(t0, t1, 15):
                        busy_cells.add(f"{t // 60:02d}:{t % 60:02d}")
                free = [s for s in day_slots if s not in busy_cells]
                if free:
                    errors.append(
                        f"fully_booked fixture: free cells remain on {fb_date} for {fb_pid}:"
                        f" {free[:3]}"
                    )

        for table in ("changes", "messages", "review_links"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n != 0:
                errors.append(f"{table} must be empty after seed, found {n}")

        if diary_bounds_ok:
            total_appts = len(all_appts)
            if total_appts:
                n_cancel = sum(1 for r in all_appts if r["status"] == "cancelled")
                share = n_cancel / total_appts
                if share < 0.03 or share > 0.08:
                    errors.append(f"cancelled share {share:.3f} outside 3-8%")

            assert diary_start is not None
            for row in all_appts:
                if row["treatment_id"] != "urgency":
                    continue
                start_u = row["start_utc"]
                try:
                    start_dt = datetime.strptime(start_u, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                except ValueError:
                    continue
                loc_d = start_dt.astimezone(MADRID).date()
                if loc_d >= diary_start:
                    errors.append(f"{row['appointment_id']}: urgency on or after diary_start")

            assert diary_end is not None
            for row in booked:
                end_dt = datetime.strptime(row["end_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                loc_start = (
                    datetime.strptime(row["start_utc"], "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=UTC)
                    .astimezone(MADRID)
                )
                if loc_start.date() >= diary_start:
                    loc_end_d = end_dt.astimezone(MADRID).date()
                    if loc_end_d > diary_end:
                        errors.append(
                            f"{row['appointment_id']}: future appointment ends after diary_end"
                        )

            try:
                occ = occupancy(path)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                for pid, buckets in occ.items():
                    if buckets.get("week1", 0) <= buckets.get("week4", 0):
                        errors.append(
                            f"{pid}: week1 occupancy {buckets.get('week1', 0):.3f}"
                            f" not greater than week4 {buckets.get('week4', 0):.3f}"
                        )

    finally:
        conn.close()

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate clinic database")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    args = parser.parse_args()
    errs = validate(args.db)
    if errs:
        for e in errs:
            print(e)
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    main()
