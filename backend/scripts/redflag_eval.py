"""Measure the red-flag matcher on a phrase set (contracts §6.2, decision D20).

    uv run python -m scripts.redflag_eval tests/redflags/tuning.yaml
    uv run python -m scripts.redflag_eval tests/redflags/tuning.yaml --json

It measures, it does not gate: whatever the figures, it exits 0. A file it cannot
read, or a malformed item, exits 2.

Recall is the share of red-flag items on which the matcher fired at least one
reason, because that is what makes the emergency phrase play. Exact recall is the
share on which it fired every reason expected. The false-alarm rate is the share of
ordinary items (`expect: []`) on which it fired anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.redflags import REASONS, RedFlagTracker

ID_PREFIXES = ("es", "en")


class SetError(Exception):
    """The phrase set is malformed."""


@dataclass(frozen=True)
class Item:
    item_id: str
    lang: str
    turns: tuple[str, ...]
    expect: frozenset[str]


@dataclass
class Result:
    item: Item
    got: frozenset[str]

    @property
    def fired(self) -> bool:
        return bool(self.got)

    @property
    def missed(self) -> bool:
        return bool(self.item.expect) and not self.item.expect <= self.got

    @property
    def false_alarm(self) -> bool:
        return not self.item.expect and self.fired


def load_items(path: Path) -> list[Item]:
    """Read and validate a phrase set; every problem raises `SetError`."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise SetError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"{path}: invalid YAML: {exc}"
        raise SetError(msg) from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        msg = f"{path}: expected a mapping with an 'items' list"
        raise SetError(msg)

    items: list[Item] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["items"], start=1):
        items.append(_item(path, index, entry, seen))
    if not items:
        msg = f"{path}: the set has no items"
        raise SetError(msg)
    return items


def _item(path: Path, index: int, entry: object, seen: set[str]) -> Item:
    where = f"{path}: item {index}"
    if not isinstance(entry, dict):
        msg = f"{where}: expected a mapping"
        raise SetError(msg)

    item_id = entry.get("id")
    if not isinstance(item_id, str) or not _id_ok(item_id):
        msg = f"{where}: bad id {item_id!r} (expected es-001 or en-001 style)"
        raise SetError(msg)
    if item_id in seen:
        msg = f"{where}: duplicate id {item_id!r}"
        raise SetError(msg)
    seen.add(item_id)

    lang = entry.get("lang")
    if lang not in ID_PREFIXES:
        msg = f"{where} ({item_id}): bad lang {lang!r} (expected es or en)"
        raise SetError(msg)
    if not item_id.startswith(f"{lang}-"):
        msg = f"{where} ({item_id}): id does not start with its lang {lang!r}"
        raise SetError(msg)

    turns = entry.get("turns")
    if (
        not isinstance(turns, list)
        or not 1 <= len(turns) <= 2
        or not all(isinstance(t, str) and t.strip() for t in turns)
    ):
        msg = f"{where} ({item_id}): turns must be a list of 1 or 2 non-empty strings"
        raise SetError(msg)

    expect = entry.get("expect", [])
    if not isinstance(expect, list) or not all(isinstance(e, str) for e in expect):
        msg = f"{where} ({item_id}): expect must be a list of reason labels"
        raise SetError(msg)
    unknown = [e for e in expect if e not in REASONS]
    if unknown:
        msg = f"{where} ({item_id}): unknown labels {', '.join(unknown)}"
        raise SetError(msg)
    if len(set(expect)) != len(expect):
        msg = f"{where} ({item_id}): expect repeats a label"
        raise SetError(msg)

    return Item(
        item_id=item_id,
        lang=lang,
        turns=tuple(turns),
        expect=frozenset(expect),
    )


def _id_ok(item_id: str) -> bool:
    prefix, _, number = item_id.partition("-")
    return prefix in ID_PREFIXES and len(number) == 3 and number.isdigit()


def run(items: list[Item]) -> list[Result]:
    """One fresh tracker per item; the reasons are the union over its turns."""
    results = []
    for item in items:
        tracker = RedFlagTracker()
        got: set[str] = set()
        for turn in item.turns:
            got.update(tracker.feed(turn))
        results.append(Result(item=item, got=frozenset(got)))
    return results


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.1f}%)" if whole else f"{part}/0 (-)"


def summary(results: list[Result]) -> dict:
    flagged = [r for r in results if r.item.expect]
    ordinary = [r for r in results if not r.item.expect]
    out: dict = {
        "items": len(results),
        "red_flag_items": len(flagged),
        "ordinary_items": len(ordinary),
        "recall": _counts(sum(1 for r in flagged if r.fired), len(flagged)),
        "exact_recall": _counts(sum(1 for r in flagged if not r.missed), len(flagged)),
        "false_alarms": _counts(sum(1 for r in ordinary if r.false_alarm), len(ordinary)),
        "per_category": {},
        "per_language": {},
    }
    for reason in REASONS:
        of_reason = [r for r in results if reason in r.item.expect]
        out["per_category"][reason] = {
            "recall": _counts(sum(1 for r in of_reason if r.fired), len(of_reason)),
            "exact_recall": _counts(sum(1 for r in of_reason if reason in r.got), len(of_reason)),
        }
    for lang in ID_PREFIXES:
        lang_flagged = [r for r in flagged if r.item.lang == lang]
        lang_ordinary = [r for r in ordinary if r.item.lang == lang]
        out["per_language"][lang] = {
            "recall": _counts(sum(1 for r in lang_flagged if r.fired), len(lang_flagged)),
            "exact_recall": _counts(
                sum(1 for r in lang_flagged if not r.missed), len(lang_flagged)
            ),
            "false_alarms": _counts(
                sum(1 for r in lang_ordinary if r.false_alarm), len(lang_ordinary)
            ),
        }
    out["misses"] = [_detail(r) for r in flagged if r.missed]
    out["false_alarm_items"] = [_detail(r) for r in ordinary if r.false_alarm]
    return out


def _counts(part: int, whole: int) -> dict:
    return {"k": part, "n": whole, "share": part / whole if whole else None}


def _detail(result: Result) -> dict:
    return {
        "id": result.item.item_id,
        "turns": list(result.item.turns),
        "expected": sorted(result.item.expect),
        "got": sorted(result.got),
    }


def _line(label: str, counts: dict) -> str:
    return f"{label}: {_share(counts['k'], counts['n'])}"


def report(path: Path, data: dict) -> list[str]:
    lines = [
        f"Red-flag matcher on {path}",
        f"{data['items']} items: {data['red_flag_items']} with a red flag,"
        f" {data['ordinary_items']} ordinary",
        "",
        _line("recall (fired at least one reason)", data["recall"]),
        _line("exact recall (fired every expected reason)", data["exact_recall"]),
        _line("false-alarm rate (fired on an ordinary item)", data["false_alarms"]),
        "",
        "Per category (recall, exact recall):",
    ]
    for reason, counts in data["per_category"].items():
        lines.append(
            f"  {reason:24} {_share(counts['recall']['k'], counts['recall']['n']):>16}"
            f"  {_share(counts['exact_recall']['k'], counts['exact_recall']['n']):>16}"
        )
    lines.append("")
    lines.append("Per language (recall, exact recall, false alarms):")
    for lang, counts in data["per_language"].items():
        lines.append(
            f"  {lang:24} {_share(counts['recall']['k'], counts['recall']['n']):>16}"
            f"  {_share(counts['exact_recall']['k'], counts['exact_recall']['n']):>16}"
            f"  {_share(counts['false_alarms']['k'], counts['false_alarms']['n']):>16}"
        )
    for title, key in (("Misses", "misses"), ("False alarms", "false_alarm_items")):
        lines.append("")
        if not data[key]:
            lines.append(f"{title}: none")
            continue
        lines.append(f"{title}:")
        for detail in data[key]:
            lines.append(f"  {detail['id']} expected {detail['expected']} got {detail['got']}")
            for turn in detail["turns"]:
                lines.append(f"    - {turn}")
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure app/redflags.py on a phrase set")
    parser.add_argument("path", type=Path, help="a YAML phrase set (tuning or holdout)")
    parser.add_argument("--json", action="store_true", help="print the figures as JSON")
    args = parser.parse_args(argv)

    try:
        items = load_items(args.path)
    except SetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    data = summary(run(items))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print("\n".join(report(args.path, data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
