"""Latency §13 helpers, bench report, and parity with the browser summariser."""

import json
import statistics

import pytest

from app.latency_report import OVERLAP_SECS, LatencySummary, pct, summarise
from scripts import bench_report

# Kept in sync with tests/js/turns.test.mjs (shared example).
SHARED_LATENCIES = [1.5, 0.1, None, 2.0, -0.05]


def test_overlap_thresholds():
    assert summarise([0.149]).overlapped == 1 and not summarise([0.149]).answered
    assert summarise([0.150]).answered == [0.150]
    assert summarise([-0.2]).overlapped == 1
    assert summarise([None]).unanswered == 1


def test_overlap_rate_formula():
    s = summarise([1.0, 0.05, 0.05, None])
    assert s.overlap_rate == pytest.approx(100 * 2 / 3)
    assert summarise([]).overlap_rate is None


def test_format_strings():
    s = summarise(SHARED_LATENCIES)
    assert (
        s.format("heard")
        == "heard: p50 1750 / p90 1950 ms (n = 2 answered, overlap 50%, 1 unanswered)"
    )
    empty_answered = summarise([0.1, None])
    assert empty_answered.format("heard").startswith("heard: p50 - / p90 - ms")
    assert summarise([]).format("heard").endswith("0 unanswered)")


def test_pct_single_value_and_inclusive():
    assert pct([2.5], 50) == 2.5
    assert pct([2.5], 90) == 2.5
    vals = [1.49, 1.5, 1.6, 2.0]
    assert pct(vals, 50) == statistics.quantiles(vals, n=100, method="inclusive")[49]


def test_shared_example_percentiles():
    s = summarise(SHARED_LATENCIES)
    assert s.p50 == pytest.approx(1.75)
    assert s.p90 == pytest.approx(1.95)
    assert s.overlapped == 2
    assert s.unanswered == 1


def test_bench_report_on_tmp_dirs(tmp_path, capsys):
    bench = tmp_path / "bench"
    calls = tmp_path / "calls"
    bench.mkdir()
    calls.mkdir()

    cid_a, cid_b, cid_c = "aaaa", "bbbb", "cccc"
    (bench / f"{cid_a}.turns.json").write_text(
        json.dumps(
            [
                {"spoke_until": 1.0, "heard_at": 2.5},
                {"spoke_until": 5.0, "heard_at": 5.08},
            ]
        )
    )
    (bench / f"{cid_b}.turns.json").write_text(
        json.dumps([{"spoke_until": 2.0, "heard_at": 3.6}, {"spoke_until": 8.0, "heard_at": None}])
    )
    (bench / f"{cid_c}.turns.json").write_text(json.dumps([{"spoke_until": 0.0, "heard_at": 1.2}]))
    (calls / f"{cid_a}.jsonl").write_text(
        json.dumps({"t": 0, "kind": "voice_config", "voice": "cascade"}) + "\n"
    )
    (calls / f"{cid_b}.jsonl").write_text(
        json.dumps({"t": 0, "kind": "call_started", "voice": "gemini"}) + "\n"
    )

    bench_report.main(["--bench-dir", str(bench), "--calls-dir", str(calls)])
    out = capsys.readouterr().out
    assert "cascade (1 calls):" in out
    assert "gemini (1 calls):" in out
    assert "? (1 calls):" in out
    assert "total:" in out
    assert "old median (overlaps included)" in out


def test_summarise_classifies_at_overlap_boundary():
    assert OVERLAP_SECS == 0.150
    s = LatencySummary(answered=[1.0], overlapped=0, unanswered=0)
    assert "overlap 0%" in s.format("heard")
    assert "overlap -," in LatencySummary(answered=[], overlapped=0, unanswered=0).format("heard")
    assert "overlap -," in s.format("server")


def test_server_latency_is_never_split_into_overlaps():
    s = summarise([0.12, 1.0], split_overlaps=False)
    assert s.answered == [0.12, 1.0] and s.overlapped == 0
    assert "overlap -," in s.format("server")


def test_call_costs_honours_the_overlapped_flag(tmp_path):
    from scripts import call_costs

    log = tmp_path / "abc.jsonl"
    events = [
        {"t": 0, "kind": "call_started", "voice": "cascade"},
        {"t": 1, "kind": "heard_latency", "secs": 0.4, "overlapped": True},
        {"t": 2, "kind": "heard_latency", "secs": 1.2},
        {"t": 3, "kind": "heard_unanswered"},
        {"t": 4, "kind": "call_ended", "duration_secs": 4},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    summary = call_costs.report(log).heard_summary
    assert (summary.answered, summary.overlapped, summary.unanswered) == ([1.2], 1, 1)


def test_bench_report_skips_truncated_log_lines(tmp_path, capsys):
    bench, calls = tmp_path / "bench", tmp_path / "calls"
    bench.mkdir()
    calls.mkdir()
    (bench / "dddd.turns.json").write_text(json.dumps([{"spoke_until": 0.0, "heard_at": 1.0}]))
    (calls / "dddd.jsonl").write_text(
        json.dumps({"t": 0, "kind": "call_started", "voice": "gemini", "now": "2026-11-07T10:00"})
        + '\n\n{"t": 1, "kin'
    )
    bench_report.main(["--bench-dir", str(bench), "--calls-dir", str(calls)])
    assert "2026-11-07 gemini" in capsys.readouterr().out
