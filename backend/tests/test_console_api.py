"""Tests for the read-only console API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import console_api
from app.console_api import (
    connect_db_ro,
    create_app,
    linear_percentile,
    main,
    parse_jsonl,
)
from tests import console_fixtures as fx


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    calls = tmp_path / "calls"
    rec = tmp_path / "recordings"
    db = tmp_path / "clinic.db"
    return calls, rec, db


@pytest.fixture
def client(dirs: tuple[Path, Path, Path]) -> TestClient:
    calls, rec, db = dirs
    app = create_app(calls, rec, db, allow_testserver=True)
    return TestClient(app, base_url="http://127.0.0.1")


def test_empty_calls_dir(client: TestClient) -> None:
    r = client.get("/api/calls")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "calls": []}


def test_missing_calls_dir(tmp_path: Path) -> None:
    calls = tmp_path / "nope"
    app = create_app(calls, tmp_path / "rec", tmp_path / "db.db", allow_testserver=True)
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.get("/api/calls").json() == {"total": 0, "calls": []}
    ov = c.get("/api/overview").json()
    assert ov["calls"]["total"] == 0
    assert ov["latency"]["text"] == "no latency data"


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}


def test_complete_call(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, db = dirs
    cid = "aabbccddeeff"
    fx.write_call(calls, cid, fx.complete_call_events())
    fx.write_wav(rec, cid, 2.0)
    fx.write_timing(rec, cid)
    fx.make_db(db)

    listing = client.get("/api/calls").json()
    assert listing["total"] == 1
    s = listing["calls"][0]
    assert s["call_id"] == cid
    assert s["started_at"] == "2026-10-25T09:00:00Z"
    assert s["channel"] == "phone"
    assert s["in_progress"] is False
    assert s["duration_secs"] == 32.0
    assert s["audio"]["available"] is True
    assert s["audio"]["secs"] == pytest.approx(2.0)

    detail = client.get(f"/api/calls/{cid}").json()
    assert detail["audio_metrics"]["latency_p50_ms"] == 1200
    lat = detail["latency"]
    assert lat["overlap_rate"] == pytest.approx(2 / 10)
    assert lat["unanswered"] == 0
    assert lat["text"] == "p50 1200 / p90 2100 ms (n = 8 answered, overlap 20%, 0 unanswered)"
    assert detail["timing"]["rate"] == 24000
    assert detail["db"]["changes"][0]["call_id"] == cid


def test_zero_answered_turns_latency(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, _db = dirs
    cid = "000011112222"
    ev = fx.complete_call_events(cid)
    for e in ev:
        if e.get("kind") == "audio_metrics":
            e["answered_turns"] = 0
            e["overlapped_turns"] = 0
            e["turns"] = 3
    fx.write_call(calls, cid, ev)
    lat = client.get(f"/api/calls/{cid}").json()["latency"]
    assert lat["p50_ms"] is None
    assert lat["text"] == "no answered turns (n = 0 answered, overlap n/a, 3 unanswered)"


def test_latency_null_without_audio_metrics(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    calls, _rec, _db = dirs
    cid = "333344445555"
    ev = [e for e in fx.complete_call_events(cid) if e.get("kind") != "audio_metrics"]
    fx.write_call(calls, cid, ev)
    detail = client.get(f"/api/calls/{cid}").json()
    assert detail["audio_metrics"] is None
    assert detail["latency"] is None


def test_in_progress_truncated(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    cid = "444455556666"
    ev = [
        {
            "t": 0,
            "kind": "call_started",
            "schema": 1,
            "now": "2026-10-01T10:00:00Z",
            "channel": "browser",
            "voice": "x",
        },
        {"t": 1, "kind": "transcript", "role": "caller", "text": "hi"},
    ]
    fx.append_truncated_call(calls, cid, ev, '{"t": 2, "kind": "trans')
    s = client.get(f"/api/calls/{cid}").json()["summary"]
    assert s["in_progress"] is True
    assert s["outcome"] is None
    assert s["counts"]["bad_lines"] == 1


def test_crashed_quota_and_views(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, db = dirs
    fx.make_db(db)
    quota_id = "aaaa11112222"
    fx.write_call(
        calls,
        quota_id,
        [
            {
                "t": 0,
                "kind": "call_started",
                "schema": 1,
                "now": "2026-10-02T10:00:00Z",
                "channel": "eval",
                "voice": "v",
            },
            {
                "t": 1,
                "kind": "provider_error",
                "provider": "x",
                "category": "quota",
                "detail": "limit",
            },
        ],
    )
    urgent_outcome = "bbbb22223333"
    fx.write_call(
        calls,
        urgent_outcome,
        [
            {
                "t": 0,
                "kind": "call_started",
                "schema": 1,
                "now": "2026-10-03T10:00:00Z",
                "channel": "phone",
                "voice": "v",
            },
            {
                "t": 1,
                "kind": "outcome",
                "result": "no_action",
                "flags": ["urgent_callback"],
            },
            {"t": 2, "kind": "call_ended", "duration_secs": 2},
        ],
    )
    db_only = "112233445566"
    fx.write_call(
        calls,
        db_only,
        [
            {
                "t": 0,
                "kind": "call_started",
                "schema": 1,
                "now": "2026-10-04T10:00:00Z",
                "channel": "bench",
                "voice": "v",
            },
            {"t": 1, "kind": "call_ended", "duration_secs": 1},
        ],
    )

    q = client.get("/api/calls", params={"view": "quota"}).json()
    assert {c["call_id"] for c in q["calls"]} == {quota_id}

    st = client.get("/api/calls", params={"view": "staff_today"}).json()
    ids = {c["call_id"] for c in st["calls"]}
    assert urgent_outcome in ids
    assert db_only in ids
    assert "same_day_urgent" in next(c for c in st["calls"] if c["call_id"] == db_only)["flags"]


def test_echo_and_event_counts(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    cid = "555566667777"
    fx.write_call(
        calls,
        cid,
        [
            {
                "t": 0,
                "kind": "call_started",
                "schema": 1,
                "now": "2026-10-05T10:00:00Z",
                "channel": "phone",
                "voice": "v",
            },
            {"t": 1, "kind": "transcript", "role": "caller", "text": "real"},
            {"t": 2, "kind": "transcript", "role": "caller", "text": "echo", "echo": True},
            {"t": 3, "kind": "transcript_missing", "speech": [1, 2], "waited_secs": 0.5},
            {
                "t": 4,
                "kind": "readback_mismatch",
                "change": "c",
                "operation_key": "k",
                "expected": "a",
                "heard": "b",
            },
            {"t": 5, "kind": "voice_unresponsive", "guard_firings": 2},
            {"t": 6, "kind": "call_ended", "duration_secs": 6},
        ],
    )
    detail = client.get(f"/api/calls/{cid}").json()
    echo_ev = [e for e in detail["events"] if e.get("echo")]
    assert len(echo_ev) == 1
    s = detail["summary"]
    assert s["counts"]["caller_turns"] == 1
    assert s["counts"]["echo_transcripts"] == 1
    assert s["counts"]["transcripts_missing"] == 1
    assert s["counts"]["readback_mismatches"] == 1
    assert "readback_mismatch" in s["flags"]
    assert "voice_unresponsive" in s["flags"]


def test_unknown_kind_passthrough(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    cid = "666677778888"
    fx.write_call(
        calls,
        cid,
        [
            {"t": 0, "kind": "call_started", "schema": 1, "now": "2026-10-06T10:00:00Z"},
            {"t": 1, "kind": "future_thing", "extra_field": 42},
        ],
    )
    ev = client.get(f"/api/calls/{cid}").json()["events"]
    assert ev[1]["kind"] == "future_thing"
    assert ev[1]["extra_field"] == 42


def test_not_found_and_missing_audio(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    cid = "777788889999"
    fx.write_call(calls, cid, fx.complete_call_events(cid))
    assert client.get("/api/calls/deadbeef0000").status_code == 404
    assert client.get(f"/api/calls/{cid}/audio").status_code == 404
    assert client.get(f"/api/calls/{cid}").json()["summary"]["audio"]["available"] is False
    assert client.get(f"/api/calls/{cid}/timing").status_code == 404


@pytest.mark.parametrize(
    "call_id",
    [
        "../etc/passwd",
        "..%2F..%2Fsecret",
        "%2e%2e",
        "/etc/passwd",
        "ABCDEF012345",
        "abc",
        "abcdef0123456",
        "abcdef012345.wav",
    ],
)
def test_path_traversal_ids(
    client: TestClient, dirs: tuple[Path, Path, Path], call_id: str
) -> None:
    _calls, rec, _db = dirs
    secret = rec.parent / "secret.txt"
    secret.write_text("TOP_SECRET_DATA", encoding="utf-8")
    for route in (
        f"/api/calls/{call_id}",
        f"/api/calls/{call_id}/audio",
        f"/api/calls/{call_id}/timing",
    ):
        r = client.get(route)
        assert r.status_code == 404
        assert "TOP_SECRET_DATA" not in r.text


def test_nul_byte_in_call_id(client: TestClient) -> None:
    r = client.get("/api/calls/abcdef012345%00")
    assert r.status_code == 404


def test_symlink_outside_recordings(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    _calls, rec, _db = dirs
    cid = "aaaabbbbcccc"
    outside_path = fx.write_wav(rec.parent, "feeebbaadccc", 0.1)
    assert outside_path.is_file()
    rec.mkdir(parents=True, exist_ok=True)
    (rec / f"{cid}.wav").symlink_to(outside_path)
    assert client.get(f"/api/calls/{cid}/audio").status_code == 404


def test_audio_range(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    _calls, rec, _db = dirs
    cid = "aa11bb22cc33"
    fx.write_wav(rec, cid, 1.0)
    fx.write_call(
        dirs[0],
        cid,
        [{"t": 0, "kind": "call_started", "schema": 1, "now": "2026-10-07T10:00:00Z"}],
    )
    r = client.get(f"/api/calls/{cid}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100


def test_appointments_dst_and_empty(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    _calls, _rec, db = dirs
    fx.make_db(db)
    r = client.get("/api/appointments", params={"date": "2026-10-25"})
    assert r.status_code == 200
    body = r.json()
    assert body["timezone"] == "Europe/Madrid"
    assert len(body["appointments"]) == 2
    assert body["absences"][0]["practitioner_id"] == "prac-sofia"
    empty = client.get("/api/appointments", params={"date": "2026-11-01"}).json()
    assert empty["appointments"] == []
    assert client.get("/api/appointments", params={"date": "not-a-date"}).status_code == 422


def test_appointments_missing_db(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, db = dirs
    missing = db.parent / "missing.db"
    assert not missing.exists()
    app = create_app(calls, rec, missing, allow_testserver=True)
    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.get("/api/appointments", params={"date": "2026-10-25"})
    assert r.status_code == 503
    assert not missing.exists()


def test_db_readonly_and_wal(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    _calls, _rec, db = dirs
    fx.make_db(db)
    conn = connect_db_ro(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO site (site_id,name,address,phone,timezone) VALUES (?,?,?,?,?)",
                ("x", "x", "x", "x", "UTC"),
            )
    finally:
        conn.close()


def test_flag_bogus_and_paging(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    for i, cid in enumerate(("111111111111", "222222222222", "333333333333", "444444444444")):
        fx.write_call(
            calls,
            cid,
            [
                {
                    "t": 0,
                    "kind": "call_started",
                    "schema": 1,
                    "now": f"2026-10-{8 + i:02d}T10:00:00Z",
                }
            ],
        )
    assert client.get("/api/calls", params={"flag": "bogus"}).status_code == 422
    page = client.get("/api/calls", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 4
    assert len(page["calls"]) == 2


def test_evil_host(client: TestClient) -> None:
    r = client.get("/health", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_main_binds_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(console_api.uvicorn, "run", fake_run)
    monkeypatch.setenv("CONSOLE_PORT", "9999")
    main()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999


def test_percentile_helper() -> None:
    assert linear_percentile([10.0, 20.0, 30.0, 40.0], 50) == pytest.approx(25.0)
    assert linear_percentile([10.0, 20.0, 30.0, 40.0], 90) == pytest.approx(37.0)
    assert linear_percentile([], 50) is None


def test_corrupt_wav_secs_null(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, _db = dirs
    cid = "ccddeeff0011"
    fx.write_corrupt_wav_header(rec, cid)
    fx.write_call(
        calls,
        cid,
        [{"t": 0, "kind": "call_started", "schema": 1, "now": "2026-10-08T10:00:00Z"}],
    )
    s = client.get("/api/calls").json()["calls"][0]
    assert s["audio"]["available"] is True
    assert s["audio"]["secs"] is None


def test_overview_with_metrics(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, db = dirs
    fx.make_db(db)
    fx.write_call(calls, "aabbccddeeff", fx.complete_call_events())
    fx.write_wav(rec, "aabbccddeeff", 1.0)
    ov = client.get("/api/overview").json()
    assert ov["calls"]["total"] == 1
    assert ov["latency"]["calls"] == 1
    assert "per-call p50:" in ov["latency"]["text"]
    assert ov["diary"]["messages_total"] == 2


def test_bad_json_line_counted(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text('{"t":1,"kind":"x"}\nnot json\n', encoding="utf-8")
    events, bad = parse_jsonl(p)
    assert len(events) == 1
    assert bad == 1


def test_get_call_db_missing(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, db = dirs
    cid = "aabbccddeeff"
    fx.write_call(calls, cid, fx.complete_call_events())
    assert not db.exists()
    detail = client.get(f"/api/calls/{cid}").json()
    assert detail["db"] is None


def test_overview_date_filter(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    fx.write_call(
        calls,
        "111111111111",
        [{"t": 0, "kind": "call_started", "schema": 1, "now": "2026-10-01T10:00:00Z"}],
    )
    fx.write_call(
        calls,
        "222222222222",
        [{"t": 0, "kind": "call_started", "schema": 1, "now": "2026-10-15T10:00:00Z"}],
    )
    no_start = "333333333333"
    (calls / f"{no_start}.jsonl").write_text(
        '{"t":0,"kind":"transcript","role":"caller","text":"x"}\n', encoding="utf-8"
    )
    all_calls = client.get("/api/overview").json()["calls"]["total"]
    assert all_calls == 3
    filtered = client.get(
        "/api/overview", params={"from": "2026-10-01", "to": "2026-10-10"}
    ).json()["calls"]["total"]
    assert filtered == 1


def test_symlinked_call_log_outside_calls_dir(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    calls, _rec, _db = dirs
    outside = calls.parent / "elsewhere"
    fx.write_call(outside, "0123456789ab", [{"t": 0, "kind": "call_started", "schema": 1}])
    calls.mkdir(parents=True, exist_ok=True)
    (calls / "0123456789ab.jsonl").symlink_to(outside / "0123456789ab.jsonl")
    assert client.get("/api/calls").json()["total"] == 0
    assert client.get("/api/calls/0123456789ab").status_code == 404


def test_appointments_compact_date_normalised(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    fx.make_db(dirs[2])
    a = client.get("/api/appointments", params={"date": "2026-10-25"}).json()
    b = client.get("/api/appointments", params={"date": "20261025"}).json()
    assert b["date"] == "2026-10-25"
    assert b["absences"] == a["absences"]
    assert b["appointments"] == a["appointments"]


def test_odd_field_types_do_not_crash(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    fx.write_call(
        calls,
        "0a0a0a0a0a0a",
        [
            {"t": 0, "kind": "call_started", "schema": "one", "now": ["x"], "channel": 5},
            {"t": 1, "kind": "outcome", "result": 3, "changes": "x", "flags": [1, "emergency"]},
            {
                "t": 2,
                "kind": "audio_metrics",
                "turns": {},
                "answered_turns": "4",
                "overlapped_turns": None,
                "latency_p50_ms": "fast",
                "dead_air_secs": "bad",
                "barge_ins": True,
            },
            {"t": 3, "kind": "call_ended", "duration_secs": "long"},
        ],
    )
    (calls / "0b0b0b0b0b0b.jsonl").write_text(
        '{"t": NaN, "kind": "call_started"}\n{"t": 1, "kind": "x", "v": Infinity}\n',
        encoding="utf-8",
    )
    listing = client.get("/api/calls")
    assert listing.status_code == 200
    by_id = {c["call_id"]: c for c in listing.json()["calls"]}
    odd = by_id["0a0a0a0a0a0a"]
    assert odd["started_at"] is None and odd["channel"] is None and odd["duration_secs"] is None
    assert odd["outcome"]["changes"] == [] and odd["flags"] == ["emergency"]
    assert by_id["0b0b0b0b0b0b"]["counts"]["bad_lines"] == 2
    detail = client.get("/api/calls/0a0a0a0a0a0a")
    assert detail.status_code == 200
    assert detail.json()["latency"]["answered"] == 0
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["audio"]["dead_air_secs"] == 0


def test_bad_recording_artifacts(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, _db = dirs
    cid = "1c1c1c1c1c1c"
    fx.write_call(calls, cid, [{"t": 0, "kind": "call_started", "schema": 1}])
    rec.mkdir(parents=True, exist_ok=True)
    (rec / f"{cid}.wav").write_bytes(b"")
    (rec / f"{cid}.timing.json").write_text("[1, 2]", encoding="utf-8")
    body = client.get(f"/api/calls/{cid}")
    assert body.status_code == 200
    assert body.json()["summary"]["audio"] == {"available": True, "secs": None}
    assert body.json()["timing"] is None
    assert client.get(f"/api/calls/{cid}/timing").status_code == 404
    (rec / f"{cid}.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    assert client.get(f"/api/calls/{cid}").json()["summary"]["audio"]["secs"] is None


@pytest.mark.parametrize(
    ("route", "expected_status"),
    [("/api/calls", 200), ("/api/overview", 200), ("/api/calls/2d2d2d2d2d2d", 404)],
)
def test_call_removed_while_reading(
    client: TestClient,
    dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    expected_status: int,
) -> None:
    calls, _rec, _db = dirs
    for cid in ("2d2d2d2d2d2d", "3e3e3e3e3e3e"):
        fx.write_call(calls, cid, [{"t": 0, "kind": "call_started", "schema": 1}])
    real = console_api.parse_jsonl

    def vanishing(path: Path) -> tuple[list[dict], int]:
        if path.name.startswith("2d2d"):
            path.unlink(missing_ok=True)  # purged after stat(), before the read
        return real(path)

    monkeypatch.setattr(console_api, "parse_jsonl", vanishing)
    r = client.get(route)
    assert r.status_code == expected_status
    if route == "/api/calls":
        assert [c["call_id"] for c in r.json()["calls"]] == ["3e3e3e3e3e3e"]
    if route == "/api/overview":
        assert r.json()["calls"]["total"] == 1


def test_sorted_by_instant_across_offsets(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    calls, _rec, _db = dirs
    fx.write_call(
        calls,
        "4f4f4f4f4f4f",
        [{"t": 0, "kind": "call_started", "now": "2026-11-10T10:00:00+02:00"}],
    )
    fx.write_call(
        calls, "5a5a5a5a5a5a", [{"t": 0, "kind": "call_started", "now": "2026-11-10T09:00:00Z"}]
    )
    ids = [c["call_id"] for c in client.get("/api/calls").json()["calls"]]
    assert ids == ["5a5a5a5a5a5a", "4f4f4f4f4f4f"]


def test_symlink_alias_inside_recordings(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, rec, _db = dirs
    fx.write_wav(rec, "6b6b6b6b6b6b", 0.1)
    fx.write_call(calls, "7c7c7c7c7c7c", [{"t": 0, "kind": "call_started", "schema": 1}])
    (rec / "7c7c7c7c7c7c.wav").symlink_to(rec / "6b6b6b6b6b6b.wav")
    assert client.get("/api/calls/7c7c7c7c7c7c/audio").status_code == 404
    assert client.get("/api/calls/6b6b6b6b6b6b/audio").status_code == 200


def test_overview_skips_p50_of_calls_without_answered_turns(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    calls, _rec, _db = dirs
    fx.write_call(
        calls,
        "8d8d8d8d8d8d",
        [
            {"t": 0, "kind": "call_started", "now": "2026-11-10T09:00:00+01:00"},
            {
                "t": 9,
                "kind": "audio_metrics",
                "turns": 2,
                "answered_turns": 0,
                "overlapped_turns": 0,
                "latency_p50_ms": 1200,
                "latency_p90_ms": 2100,
            },
        ],
    )
    lat = client.get("/api/overview").json()["latency"]
    assert lat["call_p50_ms"] == {"p50": None, "p90": None, "n_calls": 0}
    assert lat["unanswered"] == 2
    assert "0 ms" not in lat["text"]


def test_overflowing_numbers(client: TestClient, dirs: tuple[Path, Path, Path]) -> None:
    calls, _rec, _db = dirs
    huge = "9" * 400
    (calls).mkdir(parents=True, exist_ok=True)
    (calls / "9e9e9e9e9e9e.jsonl").write_text(
        '{"t": 0, "kind": "call_started", "schema": 1}\n'
        '{"t": 1, "kind": "x", "v": 1e999}\n'
        f'{{"t": 2, "kind": "audio_metrics", "turns": {huge}, "answered_turns": 1, '
        f'"overlapped_turns": 0, "latency_p50_ms": {huge}, "dead_air_secs": {huge}}}\n',
        encoding="utf-8",
    )
    detail = client.get("/api/calls/9e9e9e9e9e9e")
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["counts"]["bad_lines"] == 1
    assert body["latency"]["unanswered"] == 0
    assert body["latency"]["p50_ms"] is None
    assert client.get("/api/overview").status_code == 200
    assert client.get("/api/calls").status_code == 200


def test_missing_percentile_with_answered_turns_prints_na(
    client: TestClient, dirs: tuple[Path, Path, Path]
) -> None:
    calls, _rec, _db = dirs
    fx.write_call(
        calls,
        "aeaeaeaeaeae",
        [
            {"t": 0, "kind": "call_started", "schema": 1},
            {
                "t": 9,
                "kind": "audio_metrics",
                "turns": 3,
                "answered_turns": 3,
                "overlapped_turns": 0,
            },
        ],
    )
    lat = client.get("/api/calls/aeaeaeaeaeae").json()["latency"]
    assert lat["text"] == "p50 n/a / p90 n/a ms (n = 3 answered, overlap 0%, 0 unanswered)"
