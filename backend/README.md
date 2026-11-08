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
| `gemini` | Option A1: Gemini Live (`gemini-3.8-live`), speech to speech. Needs `GOOGLE_API_KEY` |
| `openai` | Option A2: OpenAI GPT-Live-1, speech to speech. Not built yet; refuses to start |
| `none` | Silent stub for plumbing tests |

Cascade settings: `DEEPGRAM_MODEL` (`flux-general-multi` by default, or `nova-3` with
`DEEPGRAM_LANGUAGE=multi`), `ANTHROPIC_MODEL`, `KOKORO_VOICE`. Gemini settings:
`GEMINI_MODEL`, `GEMINI_VOICE`. On the Gemini API free tier, Google may use what is
sent to improve its products, so test it with the scripted callers only.

## Measure latency and cost

Every call writes `data/calls/<call_id>.jsonl`: transcript, usage and latency per turn.

```bash
make run                                                   # in one terminal
uv run python -m scripts.voice_bench --calls 5 --max-usd 0.10   # scripted Spanish callers
make costs                                                 # latency by stage and $/min
```

The scripted caller voices patient lines with Kokoro, plays them over WebRTC and times
each answer from the end of its own speech to the first agent audio it receives. It
works with every `VOICE=`, and saves each call as a stereo WAV under
`recordings/bench/<voice>/<call_id>.wav` (git-ignored): the caller on the left, the
agent on the right, on one timeline as the caller heard it, so gaps and overlaps are
audible.
