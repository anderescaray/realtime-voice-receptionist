# Backend

Call server, voice layers and (later) the clinic engine. Python 3.12, managed with `uv`.

## Talk to it from a browser

```bash
cp ../.env.example ../.env   # fill in DEEPGRAM_API_KEY and ANTHROPIC_API_KEY
make run                     # http://localhost:7860
```

From WSL, open `http://localhost:7860` in Chrome on Windows. The page shows your
microphone level, the agent's level and the latency you hear on every turn. The first
start downloads the Kokoro voice (about 330 MB, into `~/.cache/pipecat/kokoro-onnx`).

## Voice layers

`VOICE=` picks one; all share `run_call(transport, session)` (see `app/voice/__init__.py`).

| VOICE | What it is |
|---|---|
| `cascade` | Option B: Deepgram speech-to-text, Claude Haiku, Kokoro speech on this machine |
| `s2s` | Option A: a speech-to-speech model. Not built yet; refuses to start |
| `none` | Silent stub for plumbing tests |

Cascade settings: `DEEPGRAM_MODEL` (`flux-general-multi` by default, or `nova-3` with
`DEEPGRAM_LANGUAGE=multi`), `ANTHROPIC_MODEL`, `KOKORO_VOICE`.

## Measure latency and cost

Every call writes `data/calls/<call_id>.jsonl`: transcript, usage and latency per turn.

```bash
make run                                                   # in one terminal
uv run python -m scripts.voice_bench --calls 5 --max-usd 0.10   # scripted Spanish callers
make costs                                                 # latency by stage and $/min
```

The scripted caller voices patient lines with Kokoro, plays them over WebRTC and times
each answer from the end of its own speech to the first agent audio it receives.
