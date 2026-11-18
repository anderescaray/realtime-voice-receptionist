# Backend

Call server, voice layers and (later) the clinic engine. Python 3.12, managed with `uv`.

## Seed the clinic

`make seed` deletes and rebuilds `data/clinic.db` from the static catalogue (default
anchor Monday `2026-11-16`). Use `make seed ANCHOR=YYYY-MM-DD` to shift the diary and
absences to another Monday. Every patient and phone number in the seed is synthetic.

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

## Red-flag matcher

`app/redflags.py` checks every final caller transcript for the four red flags of
contracts §6.2 (trouble breathing, swelling with fever, a blow or accident to the face
or mouth, bleeding that will not stop). It errs towards firing. Its phrase lists are
tuned on `tests/redflags/tuning.yaml` and measured on `tests/redflags/holdout.yaml`, a
set written before the lists and never used to change them:

```bash
uv run python -m scripts.redflag_eval tests/redflags/holdout.yaml
```

| Holdout measurement | Recall (red-flag phrases) | False alarms (ordinary phrases) |
|---|---|---|
| Round 1 | 51/64 (79.7%) | 3/76 (3.9%) |
| Round 2, lists frozen after tuning | 64/64 (100%) | 0/76 (0%) |
| Round 2, after review rounds 1-2 | 64/64 (100%) | 2/76 (2.6%) |
| Round 2, after review round 4 | 64/64 (100%) | 2/76 (2.6%) |

Round 2 used a fresh holdout: the 16 round-1 phrases that showed gaps moved to the
tuning set and 16 new phrases, written without seeing the lists, took their place. The
other 124 phrases were in both rounds, so only 16 phrases are fresh to round 2. The
review fixes came after the round-2 measurement and were driven by reviewers' own
probe phrases, not by the holdout.

**Known limitations.** The matcher prefers firing (decision D2), so some ordinary
replies fire, and a few phrasings still slip through:

- Missed: a past-time phrase earlier in the same clause as a fresh blow whose time
  word only comes at the very end ("el diente que me empastaron hace un mes se me ha
  roto de un golpe en la boca hoy"); a duration given alone in the next turn
  ("...y le sangra." then "Desde las nueve.").
- False alarms: a "no" right before a reassurance ("No, respiro bien."); an age or
  distance read as a temperature ("tengo 38 y la muela inflamada"); food words read
  as a blow ("una galleta"), "acabo de dar un paseo"; a baby tooth "a punto de
  caerse"; "dolor constante" in one turn and light gum bleeding in a later one; a
  blow mentioned early in the call combining with a later toothache even after the
  caller denies hitting the mouth.

**These lists are a demo default written without clinical review.** A licensed dentist
must validate the red-flag list (triage fields, phrase lists and the `emergency` text)
before any real clinic uses the agent, and the validation must be recorded in this
repository with the reviewer's role and date (contracts §6.2, D13).
