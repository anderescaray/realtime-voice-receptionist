"""Tests for eval scenarios and their facts/changes against the deterministic seed."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from evals.scenarios import SCENARIOS_DIR, SKIP_KEY, TYPES, load_scenarios, validate_shape
from seed.build import DEFAULT_ANCHOR, DEFAULT_SEED, build_db
from seed.timeutil import parse_local_date

MADRID = ZoneInfo("Europe/Madrid")

_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "i"})

_PRACTITIONER_MAP: dict[str, str] = {
    "d_iglesias": "dent_1",
    "d_moya": "dent_2",
    "d_ferrer": "dent_3",
    "d_pena": "dent_4",
    "d_torres": "hyg_1",
}

_PATIENT_RE = re.compile(r"^p_(\d{4})$")
_APPOINTMENT_RE = re.compile(r"^a_([0-9a-f]{8})$")

_HARD_CASE_IDS: tuple[str, ...] = (
    "book_known-en-02",
    "move-es-02",
    "book_known-en-01",
    "message-es-01",
    "book_known-es-02",
    "adversarial-es-02",
    "book_known-es-03",
    "red_flag-es-01",
    "red_flag-en-01",
    "red_flag-en-02",
    "adversarial-en-01",
    "adversarial-es-01",
    "language_switch-es-01",
    "language_switch-en-01",
    "urgent-en-01",
    "move-en-01",
    "move-en-02",
)

SCENARIOS = load_scenarios()
SCENARIO_IDS = [str(s.get("id", f"idx_{i}")) for i, s in enumerate(SCENARIOS)]

_EXISTING_DB_IDS: set[str] = set()


def _row_exists(conn: sqlite3.Connection | None, sql: str, row_id: str) -> bool:
    if conn is not None:
        return conn.execute(sql, (row_id,)).fetchone() is not None
    return row_id in _EXISTING_DB_IDS


def seed_id(new_id: str, conn: sqlite3.Connection | None = None) -> str:
    """Return ``new_id`` if present in the seeded DB, otherwise the pre-EN-05 id."""
    for sql in (
        "SELECT 1 FROM patients WHERE patient_id = ?",
        "SELECT 1 FROM appointments WHERE appointment_id = ?",
        "SELECT 1 FROM practitioners WHERE practitioner_id = ?",
    ):
        if _row_exists(conn, sql, new_id):
            return new_id

    if m := _PATIENT_RE.match(new_id):
        return f"p{m.group(1)}"

    if m := _APPOINTMENT_RE.match(new_id):
        digits = m.group(1)
        if not digits.startswith("000") or not digits.isdigit():
            msg = f"cannot map appointment id {new_id!r} to 5-digit seed id"
            raise ValueError(msg)
        return f"a{digits[-5:]}"

    if new_id.startswith("d_"):
        if new_id not in _PRACTITIONER_MAP:
            msg = f"unknown practitioner id: {new_id!r}"
            raise ValueError(msg)
        return _PRACTITIONER_MAP[new_id]

    msg = f"unsupported id format: {new_id!r}"
    raise ValueError(msg)


@dataclass(frozen=True)
class SeedData:
    conn: sqlite3.Connection
    meta: dict[str, str]
    fixtures: dict[str, Any]
    patients: dict[str, dict[str, Any]]
    appointments: dict[str, dict[str, Any]]
    treatment_minutes: dict[str, int]
    practitioner_treatments: dict[str, set[str]]
    working_hours: dict[str, dict[int, list[tuple[str, str]]]]
    closures: set[str]
    absences: dict[str, list[tuple[date, date]]]
    booked_by_practitioner: dict[str, list[tuple[str, str, str]]]
    booked_by_patient: dict[str, list[tuple[str, str, str]]]


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SeedData]:
    db_dir = tmp_path_factory.mktemp("eval_scenarios_seed")
    db_path = db_dir / "clinic.db"
    anchor = parse_local_date(DEFAULT_ANCHOR)
    errors = build_db(db_path, anchor, DEFAULT_SEED)
    assert errors == []

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        fixtures = json.loads(meta["fixtures"])

        pat_rows = conn.execute("SELECT * FROM patients")
        patients = {r["patient_id"]: dict(r) for r in pat_rows}

        appt_rows = conn.execute("SELECT * FROM appointments")
        appointments = {r["appointment_id"]: dict(r) for r in appt_rows}

        prac_rows = conn.execute("SELECT practitioner_id FROM practitioners")
        practitioners = {r["practitioner_id"] for r in prac_rows}

        _EXISTING_DB_IDS.clear()
        _EXISTING_DB_IDS.update(patients)
        _EXISTING_DB_IDS.update(appointments)
        _EXISTING_DB_IDS.update(practitioners)

        treat_rows = conn.execute("SELECT treatment_id, minutes FROM treatments")
        treatment_minutes = {r["treatment_id"]: int(r["minutes"]) for r in treat_rows}

        practitioner_treatments: dict[str, set[str]] = defaultdict(set)
        pt_rows = conn.execute("SELECT practitioner_id, treatment_id FROM practitioner_treatments")
        for r in pt_rows:
            practitioner_treatments[r["treatment_id"]].add(r["practitioner_id"])

        hours: dict[str, dict[int, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
        wh_sql = "SELECT practitioner_id, weekday, start_local, end_local FROM working_hours"
        for r in conn.execute(wh_sql):
            pair = (r["start_local"], r["end_local"])
            hours[r["practitioner_id"]][int(r["weekday"])].append(pair)

        closures = {r["date"] for r in conn.execute("SELECT date FROM closures")}

        absences: dict[str, list[tuple[date, date]]] = defaultdict(list)
        abs_rows = conn.execute("SELECT practitioner_id, start_date, end_date FROM absences")
        for r in abs_rows:
            span = (parse_local_date(r["start_date"]), parse_local_date(r["end_date"]))
            absences[r["practitioner_id"]].append(span)

        booked_by_practitioner: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        booked_by_patient: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for a in appointments.values():
            if a["status"] == "booked":
                item = (a["appointment_id"], a["start_utc"], a["end_utc"])
                booked_by_practitioner[a["practitioner_id"]].append(item)
                booked_by_patient[a["patient_id"]].append(item)

        yield SeedData(
            conn=conn,
            meta=meta,
            fixtures=fixtures,
            patients=patients,
            appointments=appointments,
            treatment_minutes=treatment_minutes,
            practitioner_treatments=practitioner_treatments,
            working_hours=hours,
            closures=closures,
            absences=absences,
            booked_by_practitioner=booked_by_practitioner,
            booked_by_patient=booked_by_patient,
        )
    finally:
        conn.close()


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


_FOLDS: tuple[tuple[str, str], ...] = (
    (r"g(?=[ei])", "j"),
    (r"c(?=[ei])", "s"),
    (r"z", "s"),
    (r"qu", "k"),
    (r"c(?=[aou]|[b-df-gj-np-tv-z])", "k"),
    (r"v", "b"),
    (r"ll", "y"),
    (r"(?<!c)h", ""),
    (r"y(?![aeiou])", "i"),
    (r"^x", "j"),
    (r"(.)\1+", r"\1"),
)


def _fold(word: str) -> str:
    """Spanish spelling-variant folding, in the order of contracts.md §5.10 step 3."""
    for pattern, repl in _FOLDS:
        word = re.sub(pattern, repl, word)
    return word


def _norm_words(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    words = re.findall(r"[a-z]+", stripped)
    return [_fold(w) for w in words if w not in _PARTICLES]


def _patient_matches_words(pat: dict[str, Any], spoken_words: set[str]) -> bool:
    first_words = _norm_words(pat["first_name"])
    last1_words = _norm_words(pat["last_name1"])
    if not first_words or not last1_words or first_words[0] not in spoken_words:
        return False
    return all(w in spoken_words for w in last1_words)


def _match_patients_by_name(seed: SeedData, spoken_name: str) -> list[dict[str, Any]]:
    spoken_words = set(_norm_words(spoken_name))
    return [pat for pat in seed.patients.values() if _patient_matches_words(pat, spoken_words)]


def _identify_patient(seed: SeedData, spoken_name: str, dob: str) -> list[dict[str, Any]]:
    name_matches = _match_patients_by_name(seed, spoken_name)
    return [p for p in name_matches if p["dob"] == dob]


def _hhmm_to_mins(hhmm: str) -> int:
    h, m = (int(part) for part in hhmm.split(":"))
    return h * 60 + m


def _overlaps(
    intervals: list[tuple[str, str, str]],
    start_u: str,
    end_u: str,
    ignore_id: str | None = None,
) -> bool:
    return any(aid != ignore_id and s_u < end_u and e_u > start_u for aid, s_u, e_u in intervals)


def _slot_times(d: date, t_min: int, duration: int) -> tuple[datetime, datetime, str, str]:
    start_local = datetime(d.year, d.month, d.day, t_min // 60, t_min % 60, 0, tzinfo=MADRID)
    end_local = start_local + timedelta(minutes=duration)
    start_u = start_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_u = end_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start_local, end_local, start_u, end_u


def _find_free_slots(
    seed: SeedData,
    *,
    now_dt: datetime,
    treatment: str,
    after: str,
    before: str,
    patient_id: str | None = None,
    practitioner_id: str | None = None,
    part_of_day: str | None = None,
    ignore_appointment_id: str | None = None,
) -> list[tuple[str, datetime, datetime]]:
    now_local = now_dt.astimezone(MADRID)
    today = now_local.date()
    min_date = today if treatment == "urgency" else today + timedelta(days=1)
    max_date = today + timedelta(days=28)

    after_date = date.fromisoformat(after)
    before_date = date.fromisoformat(before)

    start_date = max(min_date, after_date)
    end_date = min(max_date, before_date - timedelta(days=1))
    if start_date > end_date:
        return []

    allowed_practitioners = seed.practitioner_treatments.get(treatment, set())
    if practitioner_id is not None:
        if practitioner_id not in allowed_practitioners:
            return []
        candidate_practitioners = [practitioner_id]
    else:
        candidate_practitioners = sorted(allowed_practitioners)

    dur = seed.treatment_minutes[treatment]
    lead_cutoff = now_local + timedelta(minutes=120)
    pt_booked = seed.booked_by_patient.get(patient_id, []) if patient_id is not None else []
    slots: list[tuple[str, datetime, datetime]] = []

    cur_d = start_date
    while cur_d <= end_date:
        if cur_d.isoformat() not in seed.closures:
            weekday = cur_d.weekday()
            for pid in candidate_practitioners:
                if any(s <= cur_d <= e for s, e in seed.absences.get(pid, [])):
                    continue
                pr_booked = seed.booked_by_practitioner.get(pid, [])
                for seg_start, seg_end in seed.working_hours[pid].get(weekday, []):
                    s_min = _hhmm_to_mins(seg_start)
                    e_min = _hhmm_to_mins(seg_end)
                    t_min = s_min
                    while t_min + dur <= e_min:
                        if part_of_day == "morning" and t_min >= 14 * 60:
                            t_min += 15
                            continue
                        if part_of_day == "afternoon" and t_min < 14 * 60:
                            t_min += 15
                            continue

                        slot_start, slot_end, start_u, end_u = _slot_times(cur_d, t_min, dur)
                        if cur_d == today and slot_start < lead_cutoff:
                            t_min += 15
                            continue

                        if _overlaps(pr_booked, start_u, end_u, ignore_appointment_id):
                            t_min += 15
                            continue

                        if _overlaps(pt_booked, start_u, end_u, ignore_appointment_id):
                            t_min += 15
                            continue

                        slots.append((pid, slot_start, slot_end))
                        t_min += 15
        cur_d += timedelta(days=1)

    return slots


# 1. Count and coverage


def test_count_and_coverage() -> None:
    yaml_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    assert len(yaml_files) == 30
    assert len(SCENARIOS) == 30

    ids = [s["id"] for s in SCENARIOS]
    assert len(set(ids)) == 30
    assert ids == sorted(ids)

    for stype in TYPES:
        count = sum(1 for s in SCENARIOS if s.get("type") == stype)
        assert count >= 2, f"type {stype!r} appears {count} times (< 2)"
        langs = {s.get("language") for s in SCENARIOS if s.get("type") == stype}
        assert langs == {"es", "en"}, f"type {stype!r} is not covered in both languages"

    for lang in ("es", "en"):
        count = sum(1 for s in SCENARIOS if s.get("language") == lang)
        assert count >= 12, f"language {lang!r} appears {count} times (< 12)"

    id_set = set(ids)
    for req_id in _HARD_CASE_IDS:
        assert req_id in id_set, f"missing hard-case scenario {req_id!r}"


# 2. Shape


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_scenario_shape(scenario: dict[str, Any]) -> None:
    assert validate_shape(scenario) == []
    assert (SCENARIOS_DIR / f"{scenario['id']}.yaml").is_file()


def test_validate_shape_catches_errors() -> None:
    base = copy.deepcopy(SCENARIOS[0])
    assert validate_shape(base) == []

    with_caller_phone = copy.deepcopy(base)
    with_caller_phone["facts"]["caller_phone"] = "+442079460001"
    assert any("caller_phone" in err for err in validate_shape(with_caller_phone))

    with_skip = copy.deepcopy(base)
    with_skip[SKIP_KEY] = "temporarily skipped"
    assert validate_shape(with_skip) == []

    unpaired_reg = copy.deepcopy(base)
    unpaired_reg["expect"]["changes"] = [{"action": "register", "patient": "new"}]
    assert any("register" in err for err in validate_shape(unpaired_reg))

    book = {
        "action": "book",
        "treatment": "checkup",
        "patient": "p_0001",
        "after": "2026-11-20",
        "before": "2026-11-21",
    }
    bad_cases: list[tuple[str, Any]] = [
        ("unknown action", {**book, "action": "rebook"}),
        ("after not before", {**book, "after": "2026-11-21"}),
        ("unknown treatment", {**book, "treatment": "whitening"}),
        ("extra key", {**book, "slot": "s_00000001"}),
        ("bad patient id", {**book, "patient": "p0001"}),
    ]
    for label, change in bad_cases:
        broken = copy.deepcopy(base)
        broken["expect"]["outcome"] = "changed"
        broken["expect"]["no_action_reason"] = None
        broken["expect"]["changes"] = [change]
        assert validate_shape(broken), label

    unknown_flag = copy.deepcopy(base)
    unknown_flag["expect"]["flags"] = ["vip"]
    assert validate_shape(unknown_flag)

    reordered = dict(reversed(list(copy.deepcopy(base).items())))
    assert validate_shape(reordered)

    bad_id = copy.deepcopy(base)
    bad_id["id"] = "booking-es-01"
    assert validate_shape(bad_id)

    no_reason = copy.deepcopy(base)
    no_reason["expect"]["outcome"] = "no_action"
    no_reason["expect"]["changes"] = []
    no_reason["expect"]["no_action_reason"] = "bored"
    assert validate_shape(no_reason)


def test_loader_rejects_non_mapping(tmp_path: Path) -> None:
    (tmp_path / "empty-es-01.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_scenarios(tmp_path)


def test_name_folding_follows_contract() -> None:
    same = [
        ("Jiménez", "Giménez"),
        ("Vázquez", "Basques"),
        ("Hernández", "Ernandes"),
        ("Llorente", "Yorente"),
        ("Isabel", "Ysabel"),
        ("González", "Gonsalez"),
        ("Cecilia", "Sesilia"),
        ("Ximena", "Jimena"),
        ("Castillo", "Castiyo"),
        ("Carrasco", "Carasco"),
        ("de la Fuente", "Fuente"),
    ]
    for a, b in same:
        assert _norm_words(a) == _norm_words(b), (a, b)
    for a, b in [("Martín", "Martina"), ("Rosa", "Rosas"), ("Pérez", "Peña"), ("Luis", "Luisa")]:
        assert _norm_words(a) != _norm_words(b), (a, b)


# 3. Language/phrases/flags consistency


def _is_same_day_urgency_book(c: dict[str, Any], now_date: str) -> bool:
    if c.get("action") != "book" or c.get("treatment") != "urgency":
        return False
    return bool(c.get("after") == now_date)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_language_phrases_flags_consistency(scenario: dict[str, Any]) -> None:
    expect = scenario["expect"]
    esc = expect["escalation"]
    phrases = expect["phrases"]
    flags = expect["flags"]
    changes = expect["changes"]

    esc_is_emergency = isinstance(esc, dict) and esc.get("urgency") == "emergency"
    phrase_has_emergency = "emergency" in phrases
    flag_has_emergency = "emergency" in flags
    assert esc_is_emergency == phrase_has_emergency == flag_has_emergency

    now_date = datetime.fromisoformat(scenario["now"]).date().isoformat()
    same_day_books = [c for c in changes if _is_same_day_urgency_book(c, now_date)]
    assert ("same_day_urgent" in flags) == bool(same_day_books)
    next_day = (date.fromisoformat(now_date) + timedelta(days=1)).isoformat()
    assert all(c["before"] == next_day for c in same_day_books)

    staging_tool = {"register": "register_patient", "book": "book", "move": "move"}
    staging_tool["cancel"] = "cancel"
    for c in changes:
        assert staging_tool[c["action"]] not in expect["forbidden_tools"], c

    if "urgent_callback" in flags:
        assert expect["no_action_reason"] == "message_taken"


# 4. Facts against the seed


def _full_name(pat: dict[str, Any]) -> str:
    return " ".join(x for x in (pat["first_name"], pat["last_name1"], pat["last_name2"]) if x)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_facts_against_seed(scenario: dict[str, Any], seed_db: SeedData) -> None:
    if SKIP_KEY in scenario:
        pytest.skip(str(scenario[SKIP_KEY]))

    facts = scenario["facts"]
    changes = scenario["expect"]["changes"]
    name = facts["name"]
    dob = facts.get("dob")

    has_register = any(c.get("action") == "register" for c in changes)

    if scenario["type"] == "new_patient" and has_register:
        assert _match_patients_by_name(seed_db, name) == []
        assert "phone" in facts

    for change in changes:
        pat = change.get("patient")
        if isinstance(pat, str) and pat.startswith("p_"):
            assert dob is not None, "scenario with p_ patient must have facts.dob"
            identified = _identify_patient(seed_db, name, dob)
            assert len(identified) == 1
            assert identified[0]["patient_id"] == seed_id(pat, seed_db.conn)

    for wrong_dob in facts.get("wrong_dobs", []):
        name_matches = _match_patients_by_name(seed_db, name)
        assert len(name_matches) >= 1
        assert all(p["dob"] != wrong_dob for p in name_matches)

    if dob is not None and not has_register:
        identified = _identify_patient(seed_db, name, dob)
        assert len(identified) == 1
        if "phone" in facts:
            assert facts["phone"] == identified[0]["phone"]

    if scenario["id"] in ("book_known-es-02", "adversarial-es-02"):
        exact_matches = [p for p in seed_db.patients.values() if _full_name(p) == name]
        assert len(exact_matches) >= 2
        assert len({p["dob"] for p in exact_matches}) >= 2


# 5. Changes against the seed


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_changes_against_seed(scenario: dict[str, Any], seed_db: SeedData) -> None:
    if SKIP_KEY in scenario:
        pytest.skip(str(scenario[SKIP_KEY]))

    now_dt = datetime.fromisoformat(scenario["now"])
    facts = scenario["facts"]
    changes = scenario["expect"]["changes"]
    has_register = any(c.get("action") == "register" for c in changes)

    identified_patient: dict[str, Any] | None = None
    if "dob" in facts and not has_register:
        matches = _identify_patient(seed_db, facts["name"], facts["dob"])
        assert len(matches) == 1
        identified_patient = matches[0]

    for change in changes:
        action = change["action"]

        if action == "register":
            continue

        if action == "book":
            pat_raw = change["patient"]
            pat_id = None if pat_raw == "new" else seed_id(pat_raw, seed_db.conn)
            prac_raw = change.get("practitioner")
            prac_id = seed_id(prac_raw, seed_db.conn) if prac_raw else None
            free_slots = _find_free_slots(
                seed_db,
                now_dt=now_dt,
                treatment=change["treatment"],
                after=change["after"],
                before=change["before"],
                patient_id=pat_id,
                practitioner_id=prac_id,
                part_of_day=change.get("part_of_day"),
            )
            assert len(free_slots) >= 1, f"no free slot for book change: {change}"

        elif action == "move":
            assert identified_patient is not None
            appt_id = seed_id(change["appointment"], seed_db.conn)
            assert appt_id in seed_db.appointments
            appt = seed_db.appointments[appt_id]
            assert appt["status"] == "booked"
            assert appt["patient_id"] == identified_patient["patient_id"]
            assert _parse_utc(appt["start_utc"]) > now_dt
            assert appt["treatment_id"] == change["treatment"]

            prac_raw = change.get("practitioner")
            prac_id = seed_id(prac_raw, seed_db.conn) if prac_raw else None
            if prac_id is not None:
                assert prac_id in seed_db.practitioner_treatments[change["treatment"]]

            free_slots = _find_free_slots(
                seed_db,
                now_dt=now_dt,
                treatment=change["treatment"],
                after=change["after"],
                before=change["before"],
                patient_id=identified_patient["patient_id"],
                practitioner_id=prac_id,
                part_of_day=change.get("part_of_day"),
                ignore_appointment_id=appt_id,
            )
            assert len(free_slots) >= 1, f"no free slot for move change: {change}"

        elif action == "cancel":
            assert identified_patient is not None
            appt_id = seed_id(change["appointment"], seed_db.conn)
            assert appt_id in seed_db.appointments
            appt = seed_db.appointments[appt_id]
            assert appt["status"] == "booked"
            assert appt["patient_id"] == identified_patient["patient_id"]
            assert _parse_utc(appt["start_utc"]) > now_dt

    sid = scenario["id"]

    if sid == "book_known-es-03":
        assert identified_patient is not None
        iglesias_id = seed_id("d_iglesias", seed_db.conn)
        fb = seed_db.fixtures["fully_booked"]
        assert fb["date"] == "2026-11-18"
        assert fb["practitioner_id"] == iglesias_id
        anchor = parse_local_date(seed_db.meta["seed_anchor"])
        assert (parse_local_date("2026-11-18") - anchor).days >= 2

        wed_slots = _find_free_slots(
            seed_db,
            now_dt=now_dt,
            treatment="checkup",
            after="2026-11-18",
            before="2026-11-19",
            practitioner_id=iglesias_id,
        )
        assert wed_slots == [], "d_iglesias must have no check-up slot of her own that day"

    elif sid == "urgent-es-02":
        today_iso = now_dt.astimezone(MADRID).date().isoformat()
        tomorrow_iso = (now_dt.astimezone(MADRID).date() + timedelta(days=1)).isoformat()
        urgent_today = _find_free_slots(
            seed_db,
            now_dt=now_dt,
            treatment="urgency",
            after=today_iso,
            before=tomorrow_iso,
        )
        assert urgent_today == []

    elif sid in ("urgent-es-01", "adversarial-en-01"):
        # The caller asks for the earliest urgency visit and gets tomorrow's: nothing is left today.
        today_iso = now_dt.astimezone(MADRID).date().isoformat()
        tomorrow_iso = (now_dt.astimezone(MADRID).date() + timedelta(days=1)).isoformat()
        urgent_today = _find_free_slots(
            seed_db,
            now_dt=now_dt,
            treatment="urgency",
            after=today_iso,
            before=tomorrow_iso,
        )
        assert urgent_today == []

    elif sid == "book_known-en-02":
        # The caller first stages a Thursday 19 afternoon check-up, then switches to Friday.
        assert identified_patient is not None
        thursday = _find_free_slots(
            seed_db,
            now_dt=now_dt,
            treatment="checkup",
            after="2026-11-19",
            before="2026-11-20",
            patient_id=identified_patient["patient_id"],
            part_of_day="afternoon",
        )
        assert len(thursday) >= 1

    elif sid == "move-en-02":
        assert identified_patient is not None
        achievable_slots = _find_free_slots(
            seed_db,
            now_dt=now_dt,
            treatment="checkup",
            after="2026-11-23",
            before="2026-11-25",
            patient_id=identified_patient["patient_id"],
        )
        assert len(achievable_slots) >= 1
