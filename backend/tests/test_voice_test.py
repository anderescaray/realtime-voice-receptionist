"""Plumbing for the voice test: session log, voice-layer switch, cost report."""

import json
from datetime import UTC, datetime

import pytest

from app import voice
from app.session import CallSession
from scripts import call_costs


def events(session: CallSession) -> list[dict]:
    return [json.loads(line) for line in session.log_path.read_text().splitlines()]


def test_session_logs_start_and_finishes_once(tmp_path):
    now = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
    session = CallSession.start(call_id="c1", now=now, calls_dir=tmp_path)
    session.log("transcript", role="caller", text="Hola")
    session.finish()
    session.finish()
    kinds = [e["kind"] for e in events(session)]
    assert kinds == ["call_started", "transcript", "call_ended"]
    assert events(session)[0]["now"] == now.isoformat()


def test_unknown_voice_is_refused():
    with pytest.raises(ValueError, match="unknown VOICE"):
        voice.load("telepathy")


async def test_s2s_refuses_without_calling_a_provider(tmp_path):
    session = CallSession.start(calls_dir=tmp_path)
    with pytest.raises(NotImplementedError):
        await voice.run_call(transport=None, session=session, voice="s2s")  # type: ignore[arg-type]
    assert events(session)[-1]["kind"] == "voice_unavailable"


def test_cost_report_prices_stt_by_minute_and_llm_by_token(tmp_path):
    path = tmp_path / "c2.jsonl"
    lines = [
        {"t": 0, "kind": "voice_config", "voice": "cascade", "stt": "flux-general-multi"},
        {"t": 2, "kind": "greeting_latency", "secs": 0.9},
        {
            "t": 5,
            "kind": "usage",
            "service": "llm",
            "model": "claude-haiku-5-5",
            "input_tokens": 1_000_000,
            "output_tokens": 100_000,
        },
        {"t": 5, "kind": "usage", "service": "tts", "model": "kokoro-local", "characters": 80},
        {"t": 6, "kind": "latency", "secs": 0.7},
        {"t": 9, "kind": "latency", "secs": 0.9},
        {"t": 120, "kind": "call_ended", "duration_secs": 120.0},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    r = call_costs.report(path)
    assert r.stt_usd == pytest.approx(2 * 0.0078)
    assert r.llm_usd == pytest.approx(0.10 + 0.05)
    assert r.tts_usd == 0
    assert r.usd_per_min == pytest.approx((2 * 0.0078 + 0.15) / 2)
    assert r.latencies == [0.7, 0.9]
    assert not r.unpriced


def test_cost_report_flags_unknown_prices(tmp_path):
    path = tmp_path / "c3.jsonl"
    lines = [
        {"t": 0, "kind": "voice_config", "voice": "cascade", "stt": "nova-9"},
        {"t": 60, "kind": "call_ended", "duration_secs": 60.0},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    assert call_costs.report(path).unpriced == {"nova-9"}


def test_heard_latency_is_paired_with_its_turn_breakdown(tmp_path):
    path = tmp_path / "c4.jsonl"
    stages = [
        {"key": "endpointing_wait", "secs": 0.2},
        {"key": "llm_inference", "secs": 0.5},
        {"key": "speech_synthesis", "secs": 0.4},
    ]
    lines = [
        {"t": 0, "kind": "voice_config", "voice": "cascade", "stt": "flux-general-multi"},
        {"t": 9.0, "kind": "latency_breakdown", "measured_from": "user_silence", "stages": stages},
        {"t": 9.1, "kind": "heard_latency", "secs": 1.5},
        # A breakdown without the model in it (a misplaced turn) is not used.
        {
            "t": 19.0,
            "kind": "latency_breakdown",
            "measured_from": "user_silence",
            "stages": [{"key": "endpointing_wait", "secs": 0.2}],
        },
        {"t": 19.1, "kind": "heard_latency", "secs": 2.0},
        {"t": 30, "kind": "call_ended", "duration_secs": 30.0},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    turns = call_costs.report(path).turns
    assert [t.heard for t in turns] == [1.5, 2.0]
    assert turns[0].stages == {
        "endpointing_wait": 0.2,
        "llm_inference": 0.5,
        "speech_synthesis": 0.4,
    }
    assert turns[0].outside_server == pytest.approx(0.4)
    assert turns[1].stages == {} and turns[1].outside_server is None
