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


async def test_openai_refuses_without_calling_a_provider(tmp_path):
    session = CallSession.start(calls_dir=tmp_path)
    with pytest.raises(NotImplementedError):
        await voice.run_call(transport=None, session=session, voice="openai")  # type: ignore[arg-type]
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


def test_gemini_needs_its_key(tmp_path, monkeypatch):
    from app import config
    from app.voice import gemini

    monkeypatch.setattr(config, "GOOGLE_API_KEY", "")
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        gemini.make_llm(CallSession.start(calls_dir=tmp_path))


def test_cost_report_prices_speech_to_speech_by_modality(tmp_path):
    path = tmp_path / "c5.jsonl"
    usage = {
        "t": 5,
        "kind": "usage",
        "service": "s2s",
        "model": "gemini-3.8-live",
        "input_tokens": 1_200_000,  # 1M of it audio, 200k text
        "output_tokens": 300_000,  # 200k of it audio, 100k text
        "cache_read_tokens": 500_000,  # inside input_tokens already: not added again
        "input_audio_tokens": 1_000_000,
        "output_audio_tokens": 200_000,
    }
    lines = [
        {"t": 0, "kind": "voice_config", "voice": "gemini", "s2s": "gemini-3.8-live"},
        usage,
        {"t": 60, "kind": "call_ended", "duration_secs": 60.0},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    r = call_costs.report(path)
    assert r.label == "gemini:gemini-3.8-live"
    assert r.s2s_usd == pytest.approx(0.2 * 0.75 + 1 * 3.00 + 0.1 * 4.50 + 0.2 * 12.00)
    assert r.stt_usd == 0 and not r.unpriced
    assert (r.audio_in_tokens, r.audio_out_tokens) == (1_000_000, 200_000)


def test_steady_clock_follows_the_monotonic_clock_only(monkeypatch):
    import time

    from app import steady_clock

    steady = steady_clock.steady_time_module(anchor=1000.0)
    monkeypatch.setattr(time, "time", lambda: 9e9)  # the wall clock steps far ahead
    assert 1000.0 <= steady.time() < 1001.0
    assert steady.monotonic is time.monotonic and steady.sleep is time.sleep


def test_steady_clock_reaches_every_pipecat_module_it_names():
    import importlib
    import time

    from app import steady_clock

    steady_clock.install()
    for name in steady_clock.MODULES:
        assert importlib.import_module(name).time is not time, name
