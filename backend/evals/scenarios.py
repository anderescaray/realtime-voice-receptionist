"""Eval scenario loader and shape validator."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

SKIP_KEY = "skip"

TYPES: tuple[str, ...] = (
    "new_patient",
    "book_known",
    "move",
    "cancel",
    "urgent",
    "red_flag",
    "info",
    "message",
    "adversarial",
    "language_switch",
)

NO_ACTION_REASONS: tuple[str, ...] = (
    "info_only",
    "message_taken",
    "no_suitable_slot",
    "caller_declined",
    "not_identified",
    "out_of_scope",
    "emergency_referred",
    "technical_failure",
    "caller_hung_up",
)

ESCALATION_REASONS: tuple[str, ...] = (
    "red_flag_breathing",
    "red_flag_swelling_fever",
    "red_flag_trauma",
    "red_flag_bleeding",
    "medical_advice_request",
    "wants_human",
    "complaint",
    "other",
)

URGENCIES: tuple[str, ...] = (
    "emergency",
    "urgent",
    "routine",
)

PHRASES: tuple[str, ...] = (
    "greeting",
    "emergency",
    "recover",
    "hold",
    "still_there",
    "technical_close",
    "cap_close",
    "idle_close",
)

FLAGS: tuple[str, ...] = (
    "emergency",
    "provider_quota",
    "voice_unresponsive",
    "call_cap",
    "brain_error",
    "same_day_urgent",
    "urgent_callback",
    "readback_mismatch",
)

TOOLS: tuple[str, ...] = (
    "find_patient",
    "register_patient",
    "list_appointments",
    "search_availability",
    "book",
    "move",
    "cancel",
    "confirm_pending",
    "discard_pending",
    "record_triage",
    "take_message",
    "record_no_action",
    "clinic_info",
    "escalate",
)

TREATMENTS: tuple[str, ...] = (
    "checkup",
    "cleaning",
    "filling",
    "extraction",
    "ortho_consult",
    "urgency",
)

PARTS_OF_DAY: tuple[str, ...] = (
    "morning",
    "afternoon",
)

_TOP_KEYS: tuple[str, ...] = (
    "id",
    "type",
    "language",
    "now",
    "fixture",
    "overrides",
    "persona",
    "facts",
    "goal",
    "max_turns",
    "expect",
    "judge",
)

_EXPECT_KEYS: tuple[str, ...] = (
    "outcome",
    "changes",
    "no_action_reason",
    "escalation",
    "phrases",
    "flags",
    "forbidden_tools",
)

_BOOK_REQ = frozenset({"action", "treatment", "patient", "after", "before"})
_MOVE_REQ = frozenset({"action", "appointment", "treatment", "after", "before"})
_BOOK_MOVE_OPT = frozenset({"practitioner", "part_of_day"})

_ID_RE = re.compile(rf"^({'|'.join(TYPES)})-(es|en)-\d{{2}}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PHONE_RE = re.compile(r"^\+442079460\d{3}$")
_PATIENT_ID_RE = re.compile(r"^p_\d{4}$")
_APPOINTMENT_ID_RE = re.compile(r"^a_[0-9a-f]{8}$")
_PRACTITIONER_ID_RE = re.compile(r"^d_[a-z0-9_-]+$")
_MIN_NOW = datetime(2026, 11, 16, 0, 0, 0, tzinfo=timezone(timedelta(hours=1)))


class _ScenarioDict(dict[str, Any]):
    """Dict subclass carrying the source file stem without adding a dict key."""

    stem: str | None = None


def load_scenarios(directory: Path | None = None) -> list[dict]:
    """Load all scenario YAML files from ``directory`` sorted by ``id``."""
    target = SCENARIOS_DIR if directory is None else Path(directory)
    loaded: list[dict] = []
    for path in sorted(target.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            msg = f"{path.name}: a scenario file must hold one YAML mapping"
            raise ValueError(msg)
        item = _ScenarioDict(raw)
        item.stem = path.stem
        loaded.append(item)
    loaded.sort(key=lambda s: str(s.get("id", "")))
    return loaded


def _is_nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _is_valid_date_str(val: Any) -> bool:
    if not isinstance(val, str) or not _DATE_RE.match(val):
        return False
    try:
        date.fromisoformat(val)
    except ValueError:
        return False
    return True


def _is_valid_wrong_dobs(val: Any) -> bool:
    return isinstance(val, list) and bool(val) and all(_is_valid_date_str(d) for d in val)


def _is_str_subset(val: Any, allowed: tuple[str, ...]) -> bool:
    return isinstance(val, list) and all(isinstance(x, str) and x in allowed for x in val)


def _is_valid_judge(judge: Any) -> bool:
    # §12 judge items come on top of the default rubric; EV-01 asks at least one per scenario.
    if not isinstance(judge, list) or not (1 <= len(judge) <= 6):
        return False
    return all(_is_nonempty_str(item) for item in judge)


def _is_register(change: Any) -> bool:
    return isinstance(change, dict) and change.get("action") == "register"


def _is_book_new(change: Any) -> bool:
    if not isinstance(change, dict) or change.get("action") != "book":
        return False
    return bool(change.get("patient") == "new")


def _has_key_recursive(obj: Any, target_key: str) -> bool:
    if isinstance(obj, dict):
        if target_key in obj:
            return True
        return any(_has_key_recursive(v, target_key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key_recursive(item, target_key) for item in obj)
    return False


def _validate_change(change: Any, idx: int, problems: list[str]) -> None:
    prefix = f"expect.changes[{idx}]"
    if not isinstance(change, dict):
        problems.append(f"{prefix}: must be a dict")
        return

    action = change.get("action")
    keys = set(change.keys())

    if action == "register":
        if keys != {"action", "patient"}:
            problems.append(f"{prefix}: register keys must be {{action, patient}}, got {keys}")
        if change.get("patient") != "new":
            problems.append(f"{prefix}: register patient must be 'new'")
        return

    if action == "cancel":
        if keys != {"action", "appointment"}:
            problems.append(f"{prefix}: cancel keys must be {{action, appointment}}, got {keys}")
        appt = change.get("appointment")
        if not isinstance(appt, str) or not _APPOINTMENT_ID_RE.match(appt):
            problems.append(f"{prefix}: invalid appointment id {appt!r}")
        return

    if action in ("book", "move"):
        req = _BOOK_REQ if action == "book" else _MOVE_REQ
        missing = req - keys
        extra = keys - (req | _BOOK_MOVE_OPT)
        if missing or extra:
            problems.append(f"{prefix}: invalid keys for {action}: {sorted(keys)}")

        treatment = change.get("treatment")
        if not isinstance(treatment, str) or treatment not in TREATMENTS:
            problems.append(f"{prefix}: invalid treatment {treatment!r}")

        if action == "book":
            pat = change.get("patient")
            if not isinstance(pat, str) or (pat != "new" and not _PATIENT_ID_RE.match(pat)):
                problems.append(f"{prefix}: invalid patient id {pat!r}")
        else:
            appt = change.get("appointment")
            if not isinstance(appt, str) or not _APPOINTMENT_ID_RE.match(appt):
                problems.append(f"{prefix}: invalid appointment id {appt!r}")

        after = change.get("after")
        before = change.get("before")
        if not _is_valid_date_str(after) or not _is_valid_date_str(before):
            problems.append(f"{prefix}: after and before must be YYYY-MM-DD strings")
        elif after >= before:
            problems.append(f"{prefix}: after ({after}) must be < before ({before})")

        if "practitioner" in change:
            prac = change["practitioner"]
            if not isinstance(prac, str) or not _PRACTITIONER_ID_RE.match(prac):
                problems.append(f"{prefix}: invalid practitioner id {prac!r}")

        if "part_of_day" in change:
            pod = change["part_of_day"]
            if not isinstance(pod, str) or pod not in PARTS_OF_DAY:
                problems.append(f"{prefix}: invalid part_of_day {pod!r}")
        return

    problems.append(f"{prefix}: unknown action {action!r}")


def validate_shape(s: dict) -> list[str]:
    """Return human-readable shape problems for scenario dict ``s`` (empty if valid)."""
    if not isinstance(s, dict):
        return ["scenario must be a dict"]

    problems: list[str] = []

    if _has_key_recursive(s, "caller_phone"):
        problems.append("forbidden key 'caller_phone' present")

    keys_no_skip = tuple(k for k in s if k != SKIP_KEY)
    if keys_no_skip != _TOP_KEYS:
        problems.append(f"top-level keys must be {list(_TOP_KEYS)} in order, got {list(s.keys())}")

    if SKIP_KEY in s and not _is_nonempty_str(s[SKIP_KEY]):
        problems.append("skip must be a non-empty string")

    stype = s.get("type")
    if not isinstance(stype, str) or stype not in TYPES:
        problems.append(f"invalid type: {stype!r}")

    lang = s.get("language")
    if not isinstance(lang, str) or lang not in ("es", "en"):
        problems.append(f"invalid language: {lang!r}")

    sid = s.get("id")
    if not isinstance(sid, str) or not (m := _ID_RE.match(sid)):
        problems.append(f"invalid id format: {sid!r}")
    else:
        if m.group(1) != stype:
            problems.append(f"id type {m.group(1)!r} does not match type {stype!r}")
        if m.group(2) != lang:
            problems.append(f"id language {m.group(2)!r} does not match language {lang!r}")

    stem = getattr(s, "stem", None)
    if stem is not None and sid != stem:
        problems.append(f"id {sid!r} does not match file stem {stem!r}")

    now = s.get("now")
    if not isinstance(now, str):
        problems.append("now must be a string")
    else:
        try:
            dt = datetime.fromisoformat(now)
        except ValueError:
            problems.append(f"now is not valid ISO-8601: {now!r}")
        else:
            if not now.endswith("+01:00") or dt.utcoffset() != timedelta(hours=1):
                problems.append(f"now must have timezone offset +01:00: {now!r}")
            elif dt < _MIN_NOW:
                problems.append(f"now must be at or after 2026-11-16T00:00:00+01:00: {now!r}")

    if s.get("fixture") != "default":
        problems.append(f"fixture must be 'default', got {s.get('fixture')!r}")

    # Stricter than contracts.md §12 on purpose: every EV-01 scenario runs on the plain default
    # seed. Accept the override shape here once a scenario needs one.
    if s.get("overrides") != []:
        problems.append(f"overrides must be [], got {s.get('overrides')!r}")

    for field in ("persona", "goal"):
        if not _is_nonempty_str(s.get(field)):
            problems.append(f"{field} must be a non-empty string")

    facts = s.get("facts")
    if not isinstance(facts, dict):
        problems.append("facts must be a dict")
    else:
        if not _is_nonempty_str(facts.get("name")):
            problems.append("facts.name must be a non-empty string")
        for k, v in facts.items():
            if k == "name":
                continue
            if k == "dob":
                if not _is_valid_date_str(v):
                    problems.append(f"facts.dob must be YYYY-MM-DD, got {v!r}")
            elif k == "phone":
                if not isinstance(v, str) or not _PHONE_RE.match(v):
                    problems.append(f"facts.phone out of range: {v!r}")
            elif k == "wrong_dobs":
                if not _is_valid_wrong_dobs(v):
                    problems.append(f"facts.wrong_dobs must be a list of YYYY-MM-DD: {v!r}")
            elif not _is_nonempty_str(v):
                problems.append(f"facts.{k} must be a non-empty string, got {v!r}")

    max_turns = s.get("max_turns")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or not (8 <= max_turns <= 30):
        problems.append(f"max_turns must be an int in 8..30, got {max_turns!r}")

    expect = s.get("expect")
    if not isinstance(expect, dict):
        problems.append("expect must be a dict")
    else:
        if tuple(expect.keys()) != _EXPECT_KEYS:
            problems.append(f"expect keys must be {list(_EXPECT_KEYS)}, got {list(expect.keys())}")

        outcome = expect.get("outcome")
        changes = expect.get("changes")
        nar = expect.get("no_action_reason")

        if not isinstance(changes, list):
            problems.append("expect.changes must be a list")
        else:
            for idx, change in enumerate(changes):
                _validate_change(change, idx, problems)

            has_register = any(_is_register(c) for c in changes)
            has_book_new = any(_is_book_new(c) for c in changes)
            if has_register != has_book_new:
                msg = "register change requires a book with patient: new and vice versa"
                problems.append(msg)

        if outcome == "changed":
            if not isinstance(changes, list) or len(changes) == 0:
                problems.append("outcome 'changed' requires non-empty changes")
            if nar is not None:
                problems.append(f"outcome 'changed' requires no_action_reason: null, got {nar!r}")
        elif outcome == "no_action":
            if changes != []:
                problems.append("outcome 'no_action' requires changes == []")
            if not isinstance(nar, str) or nar not in NO_ACTION_REASONS:
                problems.append(f"outcome 'no_action' requires valid no_action_reason, got {nar!r}")
        else:
            problems.append(f"invalid expect.outcome: {outcome!r}")

        esc = expect.get("escalation")
        if esc is not None:
            if not isinstance(esc, dict) or set(esc.keys()) != {"reason", "urgency"}:
                problems.append("expect.escalation must be null or {reason, urgency}")
            else:
                if esc.get("reason") not in ESCALATION_REASONS:
                    problems.append(f"invalid escalation reason: {esc.get('reason')!r}")
                if esc.get("urgency") not in URGENCIES:
                    problems.append(f"invalid escalation urgency: {esc.get('urgency')!r}")

        phrases = expect.get("phrases")
        if not _is_str_subset(phrases, PHRASES):
            problems.append(f"expect.phrases must be a subset of PHRASES, got {phrases!r}")

        flags = expect.get("flags")
        if not _is_str_subset(flags, FLAGS):
            problems.append(f"expect.flags must be a subset of FLAGS, got {flags!r}")

        ftools = expect.get("forbidden_tools")
        if not _is_str_subset(ftools, TOOLS):
            problems.append(f"expect.forbidden_tools must be a subset of TOOLS, got {ftools!r}")

    if not _is_valid_judge(s.get("judge")):
        problems.append("judge must be a list of 1..6 non-empty strings")

    return problems
