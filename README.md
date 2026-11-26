<div align="center">

# Realtime Voice Receptionist

**An AI receptionist that answers a dental clinic's phone, in Spanish and English.**

It recognises the patient, offers real free slots and books, moves or cancels appointments, all in a live voice call.

<a href="https://github.com/anderescaray/realtime-voice-receptionist/actions/workflows/ci.yml"><img src="docs/badges/ci.svg" alt="CI" height="20"></a>
<img src="docs/badges/status.svg" alt="Status: work in progress" height="20">
<img src="docs/badges/python.svg" alt="Python 3.12" height="20">
<img src="docs/badges/react.svg" alt="React 19" height="20">
<img src="docs/badges/pipecat.svg" alt="Pipecat 1.15" height="20">

<br>

<img src="docs/demo.svg" alt="Animated demo: a caller asks for a cleaning, the agent finds the patient, checks the diary, offers Tuesday at 10:30 and books it" width="100%">

</div>

## What it does

- **Talks naturally** in real time, in Spanish or English, from the browser (phone later).
- **Books for real**: finds the patient, offers free slots from the clinic's diary, and books, moves or cancels.
- **Handles urgent pain**: asks triage questions, finds the earliest urgent slot, and tells the caller to call 112 on red flags.
- **Answers questions** about prices, hours and location, only from the clinic's own information.
- **Leaves a trail**: every call has a transcript, a recording and the reason behind each action, for staff to review.

## Why it's reliable

The model talks; **code decides**. Every booking goes through checks the model can't skip:

| Guardrail | What it prevents |
|---|---|
| Only slots the call actually looked up | Invented or already-taken times |
| Changes are staged, then saved once the caller says yes | Wrong records when someone changes their mind |
| One operation key per write, no overlapping appointments | Double bookings on retries |
| Emergency phrase played by code, not by the model | A red flag going unanswered |
| A suite of 30 simulated-caller scenarios | Regressions slipping in |

## How it works

```
Browser mic ──WebRTC──► Pipecat ──► voice model ──► brain + tools ──► clinic engine ──► SQLite
                                                                          │
                     React console ◄── call log, recording, decisions ◄───┘
```

Voice layers are swappable with `VOICE=`: `gemini` (speech-to-speech), `cascade` (Deepgram → Claude → Kokoro) or `none` for tests.

## Quick start

```bash
cp .env.example .env        # add your API keys
cd backend
make seed                   # build the demo clinic database
make run                    # open http://localhost:7860
```

More detail in [`backend/README.md`](backend/README.md).

## Stack

Python 3.12 · Pipecat · FastAPI · SQLite · React + Vite · Gemini Live / Deepgram / Claude / Kokoro

---

<sub>The demo clinic, its staff and its patients are fictional. Not for real medical use.</sub>
