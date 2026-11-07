"""Cost and latency per call, from the call logs (data/calls/*.jsonl).

    uv run python -m scripts.call_costs                 # every call
    uv run python -m scripts.call_costs data/calls/abc.jsonl

Usage is logged during the call; prices live here, dated, so an old call can be
re-priced when a provider changes its rates.
"""

import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app import config

PRICES_AS_OF = "2026-11-07"

# Speech-to-text, US$ per minute of streamed audio (Deepgram pay-as-you-go, streaming).
# The whole call is streamed, so a call is billed for its full length.
STT_PER_MIN = {
    "flux-general-en": 0.0077,
    "flux-general-multi": 0.0078,
    "nova-3": 0.0077,  # one language
    "nova-3:multi": 0.0092,  # code-switching
}

# Language models, US$ per million tokens: input, output, cache read, cache write.
# Haiku 5.5 cache rates are assumed at the usual 0.1x / 1.25x of input.
LLM_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# Speech-to-speech models, US$ per million tokens by modality (Gemini API paid tier):
# text in, audio in, text out, audio out. The free tier bills nothing; the report
# prices free-tier calls at these rates anyway, as what they would cost in production.
# Gemini reports input tokens gross of its cache, so cached tokens are not added again.
S2S_PER_MTOK = {
    "gemini-3.8-live": (0.75, 3.00, 4.50, 12.00),
}

# Kokoro runs on our own machine: no per-call charge.
TTS_PER_MCHAR = {"kokoro-local": 0.0}


# Server stages, in pipeline order, as the latency breakdown names them.
STAGES = [
    ("endpointing_wait", "silence wait (VAD)"),
    ("transcription", "final transcript"),
    ("turn_detection", "turn decision"),
    ("llm_inference", "Claude to first text"),
    ("sentence_aggregation", "first sentence complete"),
    ("speech_synthesis", "Kokoro first audio"),
    ("output_transport", "output transport"),
    ("pipeline", "pipeline hops"),
]


@dataclass
class Turn:
    """One answered turn: what the caller heard, and the server's stages for it."""

    heard: float
    stages: dict[str, float]  # empty when the server's breakdown for the turn was unusable

    @property
    def outside_server(self) -> float | None:
        """Time the server's timeline does not see: audio in, silence detection, audio out."""
        return self.heard - sum(self.stages.values()) if self.stages else None


@dataclass
class CallReport:
    call_id: str
    voice: str = "?"
    stt: str = ""
    s2s: str = ""
    secs: float = 0.0
    stt_usd: float = 0.0
    llm_usd: float = 0.0
    tts_usd: float = 0.0
    s2s_usd: float = 0.0
    audio_in_tokens: int = 0
    audio_out_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tts_chars: int = 0
    unpriced: set[str] = field(default_factory=set)
    greeting: float | None = None
    latencies: list[float] = field(default_factory=list)  # server: caller silent -> audio out
    heard: list[float] = field(default_factory=list)  # caller: stopped talking -> heard agent
    turns: list[Turn] = field(default_factory=list)

    @property
    def usd(self) -> float:
        return self.stt_usd + self.llm_usd + self.tts_usd + self.s2s_usd

    @property
    def usd_per_min(self) -> float:
        return self.usd / (self.secs / 60) if self.secs else 0.0

    @property
    def label(self) -> str:
        model = self.stt or self.s2s
        return f"{self.voice}:{model}" if model else self.voice


def stt_rate(model: str, language: str) -> float | None:
    key = f"{model}:multi" if model.startswith("nova") and language == "multi" else model
    return STT_PER_MIN.get(key)


def s2s_usd(e: dict, r: CallReport) -> float:
    """Price one speech-to-speech usage event by modality; flags unknown models."""
    audio_in = e.get("input_audio_tokens") or 0
    audio_out = e.get("output_audio_tokens") or 0
    r.input_tokens += e["input_tokens"]
    r.output_tokens += e["output_tokens"]
    r.audio_in_tokens += audio_in
    r.audio_out_tokens += audio_out
    price = S2S_PER_MTOK.get(e["model"])
    if price is None:
        r.unpriced.add(e["model"])
        return 0.0
    text_in = max(e["input_tokens"] - audio_in, 0)
    text_out = max(e["output_tokens"] - audio_out, 0)
    return (
        text_in * price[0] + audio_in * price[1] + text_out * price[2] + audio_out * price[3]
    ) / 1e6


def usable(breakdown: dict) -> bool:
    """A turn breakdown that runs from the caller's silence through Claude to audio."""
    keys = {s["key"] for s in breakdown["stages"]}
    return breakdown["measured_from"] == "user_silence" and "llm_inference" in keys


def report(path: Path) -> CallReport:
    r = CallReport(call_id=path.stem)
    stt_language = "multi"
    last_breakdown: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        kind = e["kind"]
        if kind == "voice_config":
            r.voice = e.get("voice", "?")
            r.stt = e.get("stt", "")
            r.s2s = e.get("s2s", "")
            stt_language = e.get("stt_language", stt_language)
        elif kind == "call_ended":
            r.secs = e["duration_secs"]
        elif kind == "greeting_latency":
            r.greeting = e["secs"]
        elif kind == "latency":
            r.latencies.append(e["secs"])
        elif kind == "latency_breakdown" and e.get("measured_from") == "user_silence":
            last_breakdown = e
        elif kind == "heard_latency":
            r.heard.append(e["secs"])
            stages: dict[str, float] = {}
            if last_breakdown and usable(last_breakdown) and e["t"] - last_breakdown["t"] < 3:
                for stage in last_breakdown["stages"]:
                    stages[stage["key"]] = stages.get(stage["key"], 0) + stage["secs"]
            r.turns.append(Turn(heard=e["secs"], stages=stages))
            last_breakdown = None
        elif kind == "usage" and e["service"] == "llm":
            r.input_tokens += e["input_tokens"]
            r.output_tokens += e["output_tokens"]
            price = LLM_PER_MTOK.get(e["model"])
            if price is None:
                r.unpriced.add(e["model"])
                continue
            r.llm_usd += (
                e["input_tokens"] * price[0]
                + e["output_tokens"] * price[1]
                + e.get("cache_read_tokens", 0) * price[2]
                + e.get("cache_write_tokens", 0) * price[3]
            ) / 1e6
        elif kind == "usage" and e["service"] == "s2s":
            r.s2s_usd += s2s_usd(e, r)
        elif kind == "usage" and e["service"] == "tts":
            r.tts_chars += e["characters"]
            price = TTS_PER_MCHAR.get(e["model"])
            if price is None:
                r.unpriced.add(e["model"])
            else:
                r.tts_usd += e["characters"] * price / 1e6
    if r.stt:
        rate = stt_rate(r.stt, stt_language)
        if rate is None:
            r.unpriced.add(r.stt)
        else:
            r.stt_usd = rate * r.secs / 60
    return r


def pct(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def ms(value: float | None) -> str:
    return "-" if value is None else f"{value * 1000:.0f}"


def group_table(reports: list[CallReport]) -> list[str]:
    """Markdown table of latency by stage (p50 / p90) and cost, one column per voice setup."""
    groups: dict[str, list[CallReport]] = {}
    for r in reports:
        groups.setdefault(r.label, []).append(r)
    names = list(groups)
    rows = [
        "| ms, p50 / p90 | " + " | ".join(names) + " |",
        "|---|" + "---:|" * len(names),
    ]

    def row(title: str, values_of) -> None:
        cells = []
        for name in names:
            values = values_of(groups[name])
            cells.append(f"{ms(pct(values, 50))} / {ms(pct(values, 90))}" if values else "-")
        rows.append(f"| {title} | " + " | ".join(cells) + " |")

    def turns(rs: list[CallReport]) -> list[Turn]:
        # A negative time means the agent started before the caller finished: counted apart.
        return [t for r in rs for t in r.turns if t.heard >= 0]

    row(
        "**Heard by the caller** (stops talking -> hears agent)",
        lambda rs: [t.heard for t in turns(rs)],
    )
    for key, title in STAGES:
        row(
            f"&nbsp;&nbsp;{title}",
            lambda rs, key=key: [t.stages.get(key, 0.0) for t in turns(rs) if t.stages],
        )
    row(
        "&nbsp;&nbsp;outside the server's timeline (audio in/out, VAD lag)",
        lambda rs: [t.outside_server for t in turns(rs) if t.outside_server is not None],
    )
    row(
        "Greeting (connected -> first audio out)", lambda rs: [r.greeting for r in rs if r.greeting]
    )

    def per_min(attr: str):
        def cell(rs: list[CallReport]) -> str:
            minutes = sum(r.secs for r in rs) / 60
            return f"{sum(getattr(r, attr) for r in rs) / minutes:.4f}"

        return cell

    rows.append("")
    rows.append("| | " + " | ".join(names) + " |")
    rows.append("|---|" + "---:|" * len(names))
    for title, fn in [
        ("calls", lambda rs: f"{len(rs)}"),
        (
            "answered turns (with stage breakdown)",
            lambda rs: f"{len(turns(rs))} ({sum(1 for t in turns(rs) if t.stages)})",
        ),
        (
            "agent spoke over the caller (turns)",
            lambda rs: f"{sum(1 for r in rs for t in r.turns if t.heard < 0)}",
        ),
        ("minutes", lambda rs: f"{sum(r.secs for r in rs) / 60:.1f}"),
        ("speech-to-text $/min", per_min("stt_usd")),
        ("language model $/min", per_min("llm_usd")),
        ("text-to-speech $/min", per_min("tts_usd")),
        ("speech-to-speech $/min", per_min("s2s_usd")),
        (
            "**total $/min**",
            lambda rs: f"**{sum(r.usd for r in rs) / (sum(r.secs for r in rs) / 60):.4f}**",
        ),
        ("total spent $", lambda rs: f"{sum(r.usd for r in rs):.4f}"),
    ]:
        rows.append(f"| {title} | " + " | ".join(fn(groups[n]) for n in names) + " |")
    return rows


def main(argv: list[str]) -> None:
    paths = [Path(a) for a in argv] or sorted(config.CALLS_DIR.glob("*.jsonl"))
    reports = [r for r in (report(p) for p in paths) if r.secs > 0]
    if not reports:
        print("no finished calls")
        return
    print(
        f"Prices as of {PRICES_AS_OF}, at paid-tier rates (calls on a free tier were not billed).\n"
    )
    print(
        f"{'call':12} {'setup':28} {'secs':>5} {'turns':>5} {'heard p50':>9} {'$':>7} {'$/min':>7}"
    )
    for r in reports:
        print(
            f"{r.call_id:12} {r.label:28} {r.secs:5.0f} {len(r.heard):5} "
            f"{ms(pct(r.heard, 50)):>9} {r.usd:7.4f} {r.usd_per_min:7.4f}"
            + (f"  unpriced: {', '.join(sorted(r.unpriced))}" if r.unpriced else "")
        )
    print()
    print("\n".join(group_table(reports)))


if __name__ == "__main__":
    main(sys.argv[1:])
