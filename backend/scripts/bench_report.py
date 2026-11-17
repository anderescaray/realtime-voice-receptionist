"""Heard-latency summary from bench turn files (contracts §13).

uv run python -m scripts.bench_report [--bench-dir DIR] [--calls-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from app import config
from app.latency_report import LatencySummary, summarise


def voice_for_call(calls_dir: Path, call_id: str) -> str:
    """The call's setup: start date, voice and the models it used, so runs of different
    setups are never pooled. "?" when the call's log is missing."""
    path = calls_dir / f"{call_id}.jsonl"
    if not path.is_file():
        return "?"
    day, voice, models = "", "?", []
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] == "call_started":
            day = str(e.get("now", ""))[:10]
            voice = e.get("voice", voice)
        elif e["kind"] == "voice_config":
            voice = e.get("voice", voice)
            models = [str(e[k]) for k in ("s2s", "model", "stt", "llm", "tts") if e.get(k)]
            break
    return " ".join(x for x in (day, voice, "/".join(models)) if x)


def latencies_from_turns(turns: list[dict]) -> list[float | None]:
    out: list[float | None] = []
    for t in turns:
        heard_at = t.get("heard_at")
        if heard_at is None:
            out.append(None)
        else:
            out.append(heard_at - t["spoke_until"])
    return out


def old_median_ms(latencies: list[float | None]) -> str:
    values = [v for v in latencies if v is not None]
    if not values:
        return "-"
    return f"{statistics.median(values) * 1000:.0f} ms"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench-dir", type=Path, default=config.ROOT / "data" / "bench")
    parser.add_argument("--calls-dir", type=Path, default=config.CALLS_DIR)
    args = parser.parse_args(argv)

    turn_files = sorted(args.bench_dir.glob("*.turns.json"))
    if not turn_files:
        print(f"no turn files in {args.bench_dir}")
        return

    by_voice: dict[str, list[float | None]] = {}
    calls_per_voice: dict[str, int] = {}
    for path in turn_files:
        call_id = path.name.removesuffix(".turns.json")
        turns = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(turns, list):
            continue
        voice = voice_for_call(args.calls_dir, call_id)
        latencies = latencies_from_turns(turns)
        by_voice.setdefault(voice, []).extend(latencies)
        calls_per_voice[voice] = calls_per_voice.get(voice, 0) + 1

    all_latencies: list[float | None] = []
    for voice in sorted(by_voice):
        latencies = by_voice[voice]
        all_latencies.extend(latencies)
        summary: LatencySummary = summarise(latencies)
        print(
            f"{voice} ({calls_per_voice[voice]} calls): {summary.format('heard')}  "
            f"[old median (overlaps included): {old_median_ms(latencies)}]"
        )

    total = summarise(all_latencies)
    print(f"total: {total.format('heard')}")


if __name__ == "__main__":
    main(sys.argv[1:])
