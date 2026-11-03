# clinic-voice-agent: a voice receptionist for dental clinics (build plan)

## 1. Pitch and CV line

**Pitch.** clinic-voice-agent answers a dental clinic's calls in Spanish and English, in the browser and later by phone. It recognises the patient and offers real free slots from the clinic's diary. It books, moves and cancels appointments, and handles urgent pain and questions about prices, hours and location. Every call leaves a recording, a transcript and the chain of decisions behind each action, which the clinic's staff review in a console. A suite of simulated callers tests it on every code change.

**CV line.**
> Built clinic-voice-agent, a real-time bilingual voice agent for dental clinics (Pipecat, speech-to-speech model plus a tool-calling model behind it, SQLite, React). Bookings are validated in code, never trusted to the model. A console shows each call's audio, transcript and decision trail. X% task success on 30 simulated callers in CI, p50 response latency of Y ms, about Z cents per call. Live demo at …

## 2. Design principles

| # | Practice | Why it matters |
|---|---|---|
| 1 | **The voice model and the brain are separate.** A speech model handles the conversation and hands clinic questions to a reasoning model that calls the tools. | Natural speech and correct actions need different models. |
| 2 | **One voice-layer contract:** `run_call(transport, session)`, with swappable implementations chosen by `VOICE=`, including a silent stub for tests. | Lets you try providers and compare their cost and latency without rewriting anything. |
| 3 | **Tools refuse what the call did not look up.** No booking an unknown patient, a slot that was never offered, a same-day slot, or a reason outside a fixed list. | Guardrails live in code, not in the prompt. This is the biggest reason it "really works". |
| 4 | **Changes are staged, then saved once confirmed.** A staged change can be discarded before it is saved. Each write carries a unique operation key, and the database refuses overlapping appointments. A call that changes nothing records why, from a fixed list of reasons. | A caller changing their mind never leaves a wrong record, a retry never books twice, and an empty call is explained. |
| 5 | **The clinic engine generates the slots:** a 15-minute grid within each dentist's hours, minus bookings, closures and absences. Each rule that blocks a slot is reported with its reason. **Every call runs on its own clock:** a "now" fixed when the call starts and injectable in tests. The engine and the session read only that clock, never the wall clock. | The agent can explain *why* a slot is not available instead of inventing one. Tests and evals give the same answer on every run. |
| 6 | **The prompt is rebuilt for every call:** receptionist rules, a calendar of the coming days with real dates, the clinic's catalogue, and what caller ID found. | Without real dates in the prompt, models get "next Tuesday" wrong. |
| 7 | **Every call writes a JSONL log:** transcript, tool inputs and results, staged and saved actions, outcome, and a reason when nothing was done. | You can debug any call, and it feeds the console and the evals. |
| 8 | **Stereo recording off the wire,** with caller and agent on separate channels, plus audio timing metrics: time to greeting, response latency, dead air, interruptions and how fast the agent stops talking, stutters. | Real latency and quality numbers for the README. |
| 9 | **A private staff console:** an overview, a list of calls, a review page where clicking a message jumps the recording to it and clicking a tool shows its inputs and result, and a calendar. Its API runs as a separate process that is not reachable from the internet. A visitor to the public demo sees only the review of their own call, through an unguessable link created when they hang up. | It's what a clinic would actually buy, and it's the best screenshot for recruiters. Transcripts and recordings are never public by default. |
| 10 | **A role-play Studio:** cards with a caller role and goal, so visitors know what to try in the browser demo. | Turns "talk to my bot" into a guided demo. |
| 11 | **Call hygiene:** a 5-minute cap, ending the call when it's over, and a per-visitor rate limit. Dead air gets prerecorded prompts, and silence is judged from the transcripts, not from loudness, so line noise doesn't count as speech. If a provider rejects a request for quota or credit, the caller hears a prerecorded polite message, the call ends, and the console flags it. | Protects your budget and the experience. A demo that runs out of credit fails politely instead of going silent. |
| 12 | **A cost script:** dollars per call, from logged usage. | Hiring managers love cost numbers, and dentists will ask. |
| 13 | **Honest docs:** a "Scaling up" section and the limits of the demo. | Reads as senior engineering. |

### What sets it apart

- **Evals in public.** A simulated-caller suite, results tables in the README, and a CI check on every pull request.
- **Built for dental clinics.** Treatments and how long each takes, urgent pain triage, the clinic's own wording.
- **A cost-optimised voice option.** A cheaper pipeline (speech-to-text, then a model, then text-to-speech) next to the speech-to-speech one, with the latency and cost compared.
- **Planned and built with parallel agents,** with a Kanvas export linked in the README.

## 3. Scope of the MVP (≈15–18 hours of your time)

**Demo clinic.** A fictional clinic with 1 location, 4 dentists and 1 hygienist, opening hours, holidays and absences. Treatments: check-up, cleaning, filling, extraction, orthodontics consult, urgency. Each has its own duration and the dentists who can do it, plus about 300 synthetic patients. Generate the data with a seed script that always produces the same database (`make seed`).

**Calls it handles:**
1. A new patient registers by name and books a first check-up.
2. A known patient (found from caller ID or name and date of birth) books the earliest slot for a treatment, or a slot with a given dentist or at a given time.
3. Moving or cancelling an existing appointment.
4. **Urgent pain:** triage questions, then the earliest urgency slot. The agent never gives medical advice. Red flags (swelling with fever, trouble breathing, trauma) → it tells the caller to go to the emergency department or call 112, and logs an escalation.
5. Questions about prices, hours, how to get there and insurance, answered only from the clinic's catalogue.
6. Anything else → it takes a message for a callback.

**Out of the MVP (phase 2):** a phone number, email or SMS confirmations, a real practice-software integration, more than one clinic.

## 4. Architecture

```
Browser mic ──WebRTC──► Pipecat pipeline ──► voice layer (VOICE=s2s | cascade | none)
                              │                    │ hands clinic questions to
                              │                    ▼
                              │              brain model + tools ──► CallSession (staging, validation)
                              │                                            │
                              │                                            ▼
                              │                                   clinic engine ──► SQLite
                              ├──► recorder (stereo WAV + timing)          │
                              └──► per-call JSONL ◄──────────────────────────┘
React console (overview, calls, review, calendar) ◄── read-only console API (FastAPI, separate process, private)
Public site (landing, Studio, review of your own call) ◄── call server: one call's review per unguessable link
```

**Repository layout**

```
backend/
  app/server.py          # call server (public): WebRTC sessions, /health, review by link
  app/session.py         # per-call state and clock, staging, finish() → outcome + review link
  app/tools.py           # tool definitions and refusal rules
  app/prompt.py          # prompt built for each call
  app/clinic.py          # catalogue, dated calendar
  app/voice/             # s2s.py, cascade.py, none.py (one shared contract)
  app/recorder.py        # stereo WAV + audio timeline metrics
  app/console_api.py     # read-only API for the console (own process, localhost only)
  engine/slots.py        # slot generation and the reasons a slot is blocked
  engine/store.py        # SQLite, overlap constraint, operation keys
  seed/                  # catalogue + seed script
  evals/                 # scenarios, caller simulator, checks, runner
  scripts/call_costs.py
  tests/
frontend/                # React + Vite: landing, Studio, console
contracts.md             # the agreed formats every lane builds against
kanvas.json              # the plan
```

**Tools**
- `find_patient(name?, dob?, phone?)`: returns at most 3 matches. If the details fit several people, the agent asks a follow-up question.
- `register_patient(first, last1, last2?, phone)`
- `list_appointments(patient_id)`
- `search_availability(treatment, dentist?, after?, before?, part_of_day?)`: returns slot IDs, plus which rules blocked which dentists.
- `book(slot_id, patient_id, treatment)`, `move(appointment_id, slot_id)`, `cancel(appointment_id)`: stage the change, and refuse any slot ID this call was not offered.
- `confirm_pending()`: saves the staged change once the caller has said yes, using its operation key.
- `discard_pending()`: drops the staged change before it is saved, when the caller changes their mind or says no.
- `record_no_action(reason)`: for a call that ends without a change. The reason comes from a fixed list: `info_only`, `message_taken`, `no_suitable_slot`, `caller_declined`, `not_identified`, `out_of_scope`.
- `clinic_info(topic)`: answers only from the catalogue.
- `escalate(reason, urgency)`: the reason comes from a fixed list.

## 5. Evals

1. **Unit tests** for the clinic engine: slot generation, every rule that blocks a slot, overlaps, operation keys.
2. **Simulated callers, in text:** 30 scenarios in Spanish and English, each with a persona, a goal and the expected outcome. Include hard ones: changes their mind, gives the wrong date of birth, two patients with the same name, a fully booked day, a red-flag urgency, a request for medical advice, an attempt to make the agent ignore its rules, switching language halfway. A caller model plays each one against the brain plus tools, with no audio, so a run takes minutes and costs cents. This runs in CI on every pull request.
3. **Checks:**
   - Database state afterwards: the right change, and nothing else.
   - The tool trace: confirmed before saving, no unoffered slot, a discarded change never saved, and a no-action reason from the list when nothing changed.
   - An LLM judge with a rubric: stayed in the caller's language, no medical advice, polite.
4. **An audio run** of 10 scenarios through the real voice pipeline. The caller's lines are generated with text-to-speech, and the recorder's timing metrics give p50/p95 latency, dead air and how fast the agent stops when interrupted.
5. **Cost:** `call_costs.py` over the eval runs, reported as cents per call.

The README shows three tables: task success per scenario type, latency and audio metrics, and cost per call. For each one, show the first run and the run after the fix pass.

## 6. Stack and cost

| Part | Choice | Notes |
|---|---|---|
| Real-time audio | Pipecat (open source) | Handles WebRTC now and Twilio later |
| Voice | Option A: a speech-to-speech model (OpenAI). Option B: Deepgram, then Claude Haiku, then Kokoro | Measure both on day 1. Start with whichever sounds better within budget |
| Brain | A small, fast tool-calling model (Claude Haiku class) | Calls the tools. Only short context is needed |
| Database | SQLite (WAL) | Fine on one host. The "Scaling up" doc explains the move to Postgres |
| Hosting | One small VPS (about €5/month) with Caddy for HTTPS | Voice sessions need a long-running process. Free hosting tiers fit poorly |
| Frontend | React + Vite, served by the same host | |
| Bot protection | Cloudflare Turnstile + per-IP limits + 5-minute cap | |

Expected cost: €5/month for the VPS, plus a few cents per demo call. Set a spending limit on every provider.

## 7. Timeline (your hours; parallel agents do the typing)

| Day | Hours | Milestone |
|---|---|---|
| 1 | 3 | Voice test: a Spanish browser call through Pipecat with each voice option, latency measured. Write `contracts.md`. Load the plan into Kanvas and approve it. |
| 2 | 2.5 | Parallel lanes: engine + seed, tools + session, frontend shell, eval scenarios. |
| 3 | 2.5 | Brain hand-off, prompt, recorder and JSONL log, console review page. |
| 4 | 2.5 | Connect the parts end to end, deploy to the VPS, limits. Strangers can call it. |
| 5 | 2.5 | First eval run, fix pass, CI. |
| 6 | 1.5 | Audio run, cost script, Studio role cards, calendar view. |
| 7 | 2 | README, "Scaling up" doc, 60-second demo video, launch. |

If time runs short, cut in this order: calendar view, then the audio eval run, then the cascade voice option.

## 8. How to run it on Kanvas

1. `kanvas init` in the new repo, then `python .agents/skills/kanvas/kanvas.py batch < kanvas-plan.json`. Everything arrives as proposals. Reword any task you like, then approve.
2. **Orca lanes:** run one agent per group, each in its own worktree. **In every agent's environment, set `KANVAS_BOARD` to the absolute path of `kanvas.json` in the main checkout.** Otherwise each worktree writes to its own copy of the board. Also set `KANVAS_AGENT` to the lane name (`engine`, `voice`, `console`, `evals`, `ops`), so each note says who wrote it.
3. **Agent loop:** `ready` → `start ID -m "Plan: …"` → work → `finish ID -m "what now works, what to check"`. Use `ask` for real decisions, and `attach` to add screenshots to console and Studio tasks.
4. **What YOLO covers:** turn it on for the seed, the frontend shell, deployment and the cost script. Review by hand `contracts.md`, the tools and their refusal rules, the prompt, the eval checks and the fix pass.
5. **Failing evals become tasks:** after each eval run, an agent proposes one task per failure pattern in the Launch group, and you approve the ones worth fixing.
6. **At the end:** commit `kanvas.json`, run `kanvas export docs/plan.html`, and link the export in the README.

## 9. Phase 2: making it sellable to clinics nearby

- **A Spanish phone number:** Pipecat's Twilio transport. Or keep the clinic's own number and forward its calls to the agent after hours or when the line is busy.
- **Integration with the clinic's real diary:** a Google Calendar adapter first, then their practice software if it has an API, behind the same engine interface.
- **Confirmations and reminders** by SMS, WhatsApp or email. Reminders reduce no-shows, which is what clinics pay for.
- **Transferring the call** to a human at reception.
- **Surviving a dropped voice connection:** if the speech model's connection drops mid-call, open a new session seeded with the conversation so far, and carry on without the caller noticing.
- **GDPR:** health data is a special category of personal data. You need a data processing agreement with each clinic, EU hosting, an announcement at the start of the call that it is recorded and that the caller is speaking to an AI, a retention policy, and processor agreements with your AI providers. Get advice before taking real patient calls.
- **Pricing test:** offer one clinic a free month for after-hours calls only, and measure calls answered, bookings made and no-shows.
