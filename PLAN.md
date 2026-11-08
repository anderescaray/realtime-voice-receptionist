# clinic-voice-agent: a voice receptionist for dental clinics (build plan)

## 1. Pitch and CV line

**Pitch.** clinic-voice-agent answers a dental clinic's calls in Spanish and English, in the browser and later by phone. It recognises the patient and offers real free slots from the clinic's diary. It books, moves and cancels appointments, and handles urgent pain and questions about prices, hours and location. Every call leaves a recording, a transcript and the chain of decisions behind each action, which the clinic's staff review in a console. A suite of simulated callers tests it on every code change.

**CV line.**
> Built clinic-voice-agent, a real-time bilingual voice agent for dental clinics (Pipecat, speech-to-speech model plus a tool-calling model behind it, SQLite, React). Bookings are validated in code, never trusted to the model. A console shows each call's audio, transcript and decision trail. X% task success on 30 simulated callers in CI, p50 response latency of Y ms, about Z cents per call. Live demo at …

## 2. Design principles

| # | Practice | Why it matters |
|---|---|---|
| 1 | **The voice model and the brain are separate.** A speech model handles the conversation and hands clinic questions to a reasoning model that calls the tools. | Natural speech and correct actions need different models. |
| 2 | **One voice-layer contract:** `run_call(transport, session)`, with swappable implementations chosen by `VOICE=` (`gemini`, `cascade`, `openai`, `none`), including a silent stub for tests. No business rule depends on which one is active. Audio and turn timing run on a steady clock, so a wall clock that steps cannot drop audio. | Lets you try providers and compare their cost and latency without rewriting anything. |
| 3 | **Tools refuse what the call did not look up.** No booking an unknown patient, a slot that was never offered, a same-day slot (except urgent pain, at least 2 hours ahead), a confirmation right after the caller said no, or a reason outside a fixed list. | Guardrails live in code, not in the prompt. This is the biggest reason it "really works". |
| 4 | **Changes are staged, then saved once confirmed.** A staged change can be discarded before it is saved. Each write carries a unique operation key, and the database refuses overlapping appointments. A call that changes nothing records why, from a fixed list of reasons. | A caller changing their mind never leaves a wrong record, a retry never books twice, and an empty call is explained. |
| 5 | **The clinic engine generates the slots:** a 15-minute grid within each dentist's hours, minus bookings, closures and absences. Each rule that blocks a slot is reported with its reason. **Every call runs on its own clock:** a "now" fixed when the call starts and injectable in tests. The engine, the session, the prompt and the tools read only that clock, never the wall clock. | The agent can explain *why* a slot is not available instead of inventing one. Tests and evals give the same answer on every run. |
| 6 | **The prompt is rebuilt for every call:** receptionist rules, a calendar of the coming days with real dates, and the clinic's catalogue. | Without real dates in the prompt, models get "next Tuesday" wrong. |
| 7 | **Every call writes a JSONL log:** transcript, tool inputs and results, staged and saved actions, outcome, and a reason when nothing was done. | You can debug any call, and it feeds the console and the evals. |
| 8 | **Stereo recording off the wire,** in `recordings/` (caller left, agent right, on one timeline shared with the call log), plus audio timing metrics: time to greeting, response latency, dead air, interruptions and how fast the agent stops talking, stutters. Every latency figure is reported with its overlap rate, because the median leaves out turns where caller and agent overlap. | Real latency and quality numbers for the README, and numbers that don't flatter. |
| 9 | **A private staff console:** an overview, a list of calls, a review page where clicking a message jumps the recording to it and clicking a tool shows its inputs and result, and a calendar. Its API runs as a separate process that is not reachable from the internet. A visitor to the public demo is told before the call, on the page and in the opening, that the call is recorded and that they are talking to an AI, and the page warns them, before the microphone is requested, not to give real names, health data or phone numbers. They see only the review of their own call, through an unguessable link created when they hang up; when the link expires, the recording and the call's data are deleted with it. | It's what a clinic would actually buy, and it's the best screenshot for recruiters. Transcripts and recordings are never public by default. |
| 10 | **A role-play Studio:** cards with a caller role and goal, so visitors know what to try in the browser demo. | Turns "talk to my bot" into a guided demo. |
| 11 | **Call hygiene:** a 5-minute cap, ending the call when it's over, and a per-visitor rate limit. Dead air gets prerecorded prompts, and silence is judged from the transcripts, not from loudness, so line noise doesn't count as speech. A silence guard in code plays a fixed recovery phrase when the caller has finished and the agent doesn't answer in time, so no instruction ("be quiet") can leave a call mute; if recovery fails, the call ends politely and is flagged. If a provider rejects a request for quota or credit, the caller hears a prerecorded polite message with the clinic's phone number and the 112 reminder, the call ends, and the console flags it. | Protects your budget and the experience. A demo that runs out of credit fails politely instead of going silent. |
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
2. A known patient (always identified by name and date of birth) books the earliest slot for a treatment, or a slot with a given dentist or at a given time.
3. Moving or cancelling an existing appointment.
4. **Urgent pain:** triage questions, then the earliest urgency slot, the same day if it starts at least 2 hours ahead (configurable); same-day urgent bookings are flagged for staff in the console. With no slot, the agent takes a message for an urgent callback. The agent never gives medical advice. Red flags (swelling with fever, trouble breathing, trauma, bleeding that will not stop) → the caller is told to go to the emergency department or call 112, and an escalation is logged. The 112 instruction is a fixed, conditional phrase played by code ("if you have trouble breathing..., call 112"), triggered by the brain's triage or by a red-flag check on the caller's transcript, never left to the voice model: in the voice test the speech-to-speech model answered a toothache without mentioning 112. The red-flag list is a demo default; a licensed dentist must validate it before any real clinic uses it.
5. Questions about prices, hours, how to get there and insurance, answered only from the clinic's catalogue.
6. Anything else → it takes a message for a callback.

**Out of the MVP (phase 2):** a phone number, email or SMS confirmations, a real practice-software integration, more than one clinic.

## 4. Architecture

```
Browser mic ──WebRTC──► Pipecat pipeline ──► voice layer (VOICE=gemini | cascade | openai | none)
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

The exact formats every lane builds against (tools, events, tables, clocks, fixed phrases, the silence guard) are in `contracts.md`.

**Repository layout**

```
backend/
  app/server.py          # call server (public): WebRTC sessions, /health, review by link
  app/session.py         # per-call state and clock, staging, finish() → outcome + review link
  app/tools.py           # tool definitions and refusal rules
  app/prompt.py          # prompt built for each call
  app/clinic.py          # catalogue, dated calendar
  app/voice/             # gemini.py, openai.py, cascade.py, none.py (one shared contract), common.py (shared plumbing)
  app/steady_clock.py    # monotonic time() for Pipecat's audio and turn timing
  app/recorder.py        # stereo WAV + audio timeline metrics
  app/console_api.py     # read-only API for the console (own process, localhost only)
  engine/slots.py        # slot generation and the reasons a slot is blocked
  engine/store.py        # SQLite, overlap constraint, operation keys
  seed/                  # catalogue + seed script
  evals/                 # scenarios, caller simulator, checks, runner
  scripts/call_costs.py
  scripts/voice_bench.py # scripted callers over WebRTC: heard latency, stereo WAV per call
  tests/
frontend/                # React + Vite: landing, Studio, console
contracts.md             # the agreed formats every lane builds against
kanvas.json              # the plan
recordings/              # stereo WAV per call (git-ignored)
```

**Tools**
- `find_patient(name, dob)`: a patient is identified only when exactly one name match has that date of birth, and only that patient's data is returned; otherwise just a match count, never another patient's name. Names are matched ignoring case and accents, with tested tolerance for common Spanish spelling variants. At most 3 failed date-of-birth attempts per call. If the details fit several people, the agent asks a follow-up question.
- `register_patient(first, last1, last2?, dob, phone)`: staged, and saved with the first booking
- `list_appointments(patient_id)`
- `search_availability(treatment, dentist?, after?, before?, part_of_day?)`: returns slot IDs, plus which rules blocked which dentists.
- `book(slot_id, patient_id, treatment)`, `move(appointment_id, slot_id)`, `cancel(appointment_id)`: stage the change, and refuse any slot ID this call was not offered.
- `confirm_pending()`: saves the staged change, using its operation key, only after a caller turn that came after staging and does not contain a clear no ("no", "espera", "wait", "cancel"...). The model is told to call it only after a yes; the eval judge checks the yes. After a "no" the agent asks a closed yes-or-no question, and a second "no" in a row discards the change. A transcript of the agent's own voice echoed back through the caller's microphone never counts as the caller's reply. The date and time the agent read back are compared with the staged change and a mismatch is flagged in the console.
- `discard_pending()`: drops the staged change before it is saved, when the caller changes their mind or says no.
- `record_no_action(reason)`: for a call that ends without a change. The reason comes from a fixed list: `info_only`, `message_taken`, `no_suitable_slot`, `caller_declined`, `not_identified`, `out_of_scope`, `emergency_referred`, `technical_failure`, `caller_hung_up`.
- `record_triage(...)`: stores the urgent-pain triage answers; a red flag makes code play the emergency phrase.
- `take_message(name, phone, text, urgent?)`: saves a callback message for staff.
- `clinic_info(topic)`: answers only from the catalogue.
- `escalate(reason, urgency)`: the reason comes from a fixed list.

## 5. Evals

1. **Unit tests** for the clinic engine: slot generation, every rule that blocks a slot, overlaps, operation keys.
2. **Simulated callers, in text:** 30 scenarios in Spanish and English, each with a persona, a goal and the expected outcome. Include hard ones: changes their mind, gives the wrong date of birth, two patients with the same name, a fully booked day, a red-flag urgency, a request for medical advice, an attempt to make the agent ignore its rules, switching language halfway. A caller model plays each one against the brain plus tools, with no audio, so a run takes minutes and costs cents. This runs in CI on every pull request. It measures the brain's decisions, not what a speech-to-speech voice actually says.
3. **Checks:**
   - Database state afterwards: the right change, and nothing else.
   - The tool trace: confirmed before saving, no unoffered slot, a discarded change never saved, and a no-action reason from the list when nothing changed.
   - An LLM judge with a rubric: stayed in the caller's language, no medical advice, polite.
4. **An audio run** of 10 scenarios through the real voice pipeline. The caller's lines are generated with text-to-speech, and the recorder's timing metrics give p50/p90 latency with the overlap rate beside it, dead air and how fast the agent stops when interrupted. This run also checks what the voice actually said: the read-back of each change against what was staged, and any date, time, price or name that no tool gave.
5. **Cost:** `call_costs.py` over the eval runs, reported as cents per call.

The README shows three tables: task success per scenario type, latency and audio metrics, and cost per call. For each one, show the first run and the run after the fix pass.

## 6. Stack and cost

| Part | Choice | Notes |
|---|---|---|
| Real-time audio | Pipecat (open source) | Handles WebRTC now and Twilio later |
| Voice | A1: Gemini Live (speech to speech). A2: OpenAI GPT-Live-1 (speech to speech). B: Deepgram Flux, then Claude Haiku 5.5, then Kokoro on the host | Measured on day 1 (heard latency p50 1.5 s for A1, 2.5 s for B, overlap rate still to be measured in VO-06; about $0.027 and $0.008 per minute). Provisional: A1 primary, B fallback, until A2 is measured in the same harness |
| Brain | Claude Haiku 5.5 | Calls the tools. Only short context is needed. In the cascade, the text model is the brain |
| Database | SQLite (WAL) | Fine on one host. The "Scaling up" doc explains the move to Postgres |
| Hosting | One small VPS (about €5/month) with Caddy for HTTPS | Voice sessions need a long-running process. Free hosting tiers fit poorly |
| Provider tiers | Paid or no-training terms for every provider a public call reaches | Launch requirement: the public demo never runs on a free tier whose data the provider may use |
| Frontend | React + Vite, served by the same host | |
| Bot protection | Cloudflare Turnstile + per-IP limits + 5-minute cap | |

Expected cost: €5/month for the VPS, plus a few cents per demo call. Set a spending limit on every provider.

## 7. Timeline (your hours; parallel agents do the typing)

| Day | Hours | Milestone |
|---|---|---|
| 1 | 3 | Voice test: a Spanish browser call through Pipecat with each voice option, latency measured (done: A1 and B; A2 later). Write `contracts.md`. Load the plan into Kanvas and approve it. |
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
- **Caller ID:** use the calling number as a hint to find the patient faster. The MVP always identifies by name and date of birth, because caller ID can be spoofed or shared within a family.
- **Clinical sign-off:** a licensed dentist validates the red-flag list and the triage questions before any real clinic uses the agent.
- **Surviving a dropped voice connection:** if the speech model's connection drops mid-call, open a new session seeded with the conversation so far, and carry on without the caller noticing.
- **GDPR:** health data is a special category of personal data. You need a data processing agreement with each clinic, EU hosting, the announcement that the call is recorded and that the caller is speaking to an AI (the demo already plays it), a retention policy (the demo deletes each call after 7 days), and processor agreements with your AI providers. Get advice before taking real patient calls.
- **Pricing test:** offer one clinic a free month for after-hours calls only, and measure calls answered, bookings made and no-shows.
