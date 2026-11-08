# Contracts

The formats and rules every lane builds against. PLAN.md says what we build and why; this file says exactly how the parts fit together. When the two disagree, this file wins for interfaces and PLAN.md wins for scope.

**Changing this file.** After FD-02 is approved, any change needs a note on the board, on the task that needs it, saying what changed and which lanes it affects. Additive changes (a new optional field, a new event kind) are fine with a note. Renaming or removing something, or changing a meaning, needs the human's approval first.

**Status of each part.** "Built" means FD-01 code already does it and the code is the reference. "To build" means the task named next to it builds it against this file.

---

## 1. Principles that bind every lane

1. **No business rule depends on which voice layer is active.** Identification, slot validity, staging, confirmation, red flags, the silence guard, the call cap and the outcome all live in the session, the tools, the engine or the shared call plumbing (`app/voice/common.py`). A voice layer only moves audio and text, and hands clinic questions to the brain.
2. **Safety-critical speech is played by code, never generated.** The emergency instruction, silence recovery, quota and technical-failure messages are fixed phrases (§6), played as prerecorded clips. A model may say similar things in its own words, but the guarantee does not rely on it.
3. **Tools never raise.** Every tool returns a JSON object. A refusal is a normal result with an error code from a closed list and a message the model can say to the caller in its own words.
4. **Every call reads one clock** (§2). No code under `backend/engine/`, and no code in `app/session.py`, `app/tools.py`, `app/prompt.py` or `app/clinic.py`, reads the wall clock.
5. **Closed vocabularies are closed.** Reason codes, topics, treatments, error codes and event kinds are listed here. A value outside the list is refused, never stored.

---

## 2. Clocks

There are two clocks, for two different jobs.

| Clock | What it is | Who reads it | Status |
|---|---|---|---|
| **Call clock** `session.now` | A timezone-aware `datetime` in the clinic's zone (`Europe/Madrid`), fixed when the call starts and constant for the whole call. Tests and evals pass it in: `CallSession.start(now=...)`. | Engine, session, tools, prompt, catalogue: anything about dates, "today", "tomorrow", same-day rules, the dated calendar | Built (fixed at start). To build: clinic zone instead of UTC (EN-04) |
| **Timing clock** | `time.monotonic()`, as seconds since the call started. It is the `t` of every log event (§8) and sample 0 of the recording (§9). | Call log, recorder, silence guard, dead-air prompts, call cap, metrics | Built |

Rules:

- **"Today"** is `session.now.date()` in the clinic's zone. A call that runs past midnight still uses the date it started on (the call cap keeps this to minutes).
- Engine functions take `now` as a parameter. They have no default that reads the wall clock.
- Only `CallSession.start()` may read the wall clock, and only when no `now` was passed. A test (`tests/test_no_wall_clock.py`, EN-04) scans the files in principle 4 for `datetime.now(`, `datetime.utcnow(`, `date.today(` and `time.time(`, and fails if any appear.
- **Steady clock for audio.** Pipecat's WebRTC transport and turn detection time durations with `time.time()`. `app/steady_clock.install()` gives those modules a `time()` that moves only with the monotonic clock, so a wall clock that steps cannot drop agent audio or misjudge a turn. The call server installs it before any call. Any new Pipecat module that times audio or turns with `time.time()` is added to `steady_clock.MODULES`. (Built.)

---

## 3. Voice layer

### 3.1 Choosing a layer

`VOICE=` picks a module `app/voice/<name>.py`. Allowed values (`app/voice/__init__.py`, `LAYERS`):

| VOICE | Option | What it is | Status |
|---|---|---|---|
| `gemini` | A1 | Gemini Live, speech to speech | Built (no tools yet). **Primary, provisional** |
| `cascade` | B | Deepgram Flux speech-to-text, Claude Haiku 5.5, Kokoro speech on the host | Built (no tools yet). **Fallback** |
| `openai` | A2 | OpenAI GPT-Live-1, speech to speech | Slot only: refuses to start. Measured in VO-05 |
| `none` | – | Silent stub: wires audio in and out, says nothing | Built. Plumbing tests only |

The choice of primary and fallback is provisional until VO-05 measures A2 in the same harness. Today's numbers (FD-01, scripted callers): heard latency p50/p90 1490/1991 ms for A1, 2490/3281 ms for B; about $0.027 per minute for A1 at paid rates, rising with call length, and $0.008 for B.

An unknown `VOICE` value fails at start-up with the list of allowed values.

### 3.2 The contract

Each layer exposes:

```python
async def run_call(transport: BaseTransport, session: CallSession) -> None
```

A layer must:

1. Greet the caller with `session.greeting` as soon as the transport connects.
2. Use `session.instructions()` as its system prompt (the per-call prompt, VO-01).
3. Hand every clinic question to the brain (§4). The speech-to-speech layers get exactly one tool, `ask_clinic`; the cascade gets the clinic tools directly (§4.3).
4. Log both sides' speech as `transcript` events with `role` `"caller"` or `"agent"` (§8). Caller transcripts are required: red-flag detection, the silence guard and dead-air prompts all read them.
5. Log what the call consumed as `usage` events.
6. Run through the shared plumbing in `app/voice/common.py` (`run_pipeline`), which owns the call cap, the silence guard, dead-air prompts, fixed-phrase playback, provider-error handling and the recorder taps. A layer must not re-implement any of them.
7. Report provider errors through the shared plumbing, with a category (§8, `provider_error`).
8. Return when the caller hangs up, the call is cut, or the plumbing ends it. **Never call `session.finish()`**: the server calls it after `run_call` returns, also when the layer raised.

The `none` stub keeps 1, 4 and 8 only as far as they apply to silence: it greets with nothing and logs only the hang-up.

### 3.3 What the shared plumbing gives every layer (to build in VO-02 and VO-03)

```python
class CallControl:
    async def play(self, phrase_id: str) -> None   # §6; agent channel; holds model audio until the clip ends
    async def nudge(self) -> None                   # ask the voice model to answer the last caller turn now
    async def end(self, phrase_id: str | None) -> None  # optional closing clip, then hang up
    def busy(self, reason: str) -> ContextManager   # brain or tool working: holds the silence guard (§7)
```

- `play` is the only way fixed phrases reach the caller. While a clip plays, audio from the voice model is held, then released when the clip ends, so the two never overlap. After the clip, the voice model's context gets a short system note saying what was said (for example "The caller was told to call 112"), so it does not contradict or repeat it.
- `nudge` triggers a new response from the model on the conversation so far: `LLMRunFrame` for the cascade, a response request for the speech-to-speech layers. A layer that cannot nudge must say so with `supports_nudge = False`; the silence guard then skips straight to its failure step (§7.3).

---

## 4. Brain hand-off

### 4.1 Speech-to-speech layers: one tool, `ask_clinic`

The voice model talks; the brain (Claude Haiku 5.5) decides and calls the clinic tools (§5). The voice model's only tool:

```json
{
  "name": "ask_clinic",
  "description": "Ask the clinic system about patients, appointments, availability, prices, hours, location, insurance, urgent pain, or to make a change. Always use it before stating any clinic fact.",
  "parameters": {
    "type": "object",
    "properties": {
      "request": {"type": "string", "description": "What the caller wants, with every detail they gave, in their words"},
      "language": {"type": "string", "enum": ["es", "en"]}
    },
    "required": ["request", "language"]
  }
}
```

The brain runs a tool loop (at most 6 tool calls per request) and returns:

```json
{"say": "text for the voice to say in its own words", "phrase": null}
```

- `say` is always present and short (two sentences at most). It is what the brain wants the caller to hear next, often a question.
- `phrase` is a fixed phrase ID (§6) that code has **already played** before the reply reached the voice model, or `null`. The voice model must not repeat it.
- The brain keeps its own conversation history for the call (its requests, tool calls and replies). It does not see the audio.
- `ask_clinic` never raises. If the brain fails or times out (`BRAIN_TIMEOUT_SECS`, default 12 s), it returns `{"say": "<apology and ask to repeat>", "phrase": null, "error": "brain_unavailable"}` and logs `brain_error`.
- While the brain works, the plumbing holds the silence guard (§7) and plays `hold` once if the brain takes longer than `HOLD_PHRASE_SECS` (default 2.5 s).

### 4.2 What lives where

| Concern | Voice model prompt | Brain prompt | Code |
|---|---|---|---|
| Persona, language, short spoken replies | yes | – | – |
| Receptionist rules, dated calendar, catalogue, caller-ID facts | – | yes | built per call (VO-01) |
| Identification, slot validity, staging, confirmation | – | asked to follow them | enforced (§5) |
| Urgent pain triage questions | – | yes | triage required before an urgency slot (§5) |
| Red flags → 112 | may mention it | yes | **guaranteed** (§6.2) |

### 4.3 The cascade

In the cascade the text model already reads the caller's words, so it **is** the brain: it gets the clinic tools (§5) directly, with the brain prompt plus the spoken-reply rules. There is no second hop. Every rule in §5, §6 and §7 applies unchanged, because they are in code. (See Decisions for review, D6.)

---

## 5. Tools

### 5.1 Shape

- Each tool is a flat spec `{"name", "description", "parameters"}`, where `parameters` is JSON Schema (draft 2020-12, `type: object`, `additionalProperties: false`). `app/tools.py` exports `tools_for_session(session) -> list[dict]` and `async call_tool(session, name, args) -> dict`.
- `call_tool` never raises. Unknown tool, bad JSON or a schema violation returns `{"ok": false, "error": "bad_arguments", "message": ...}`.
- A success is `{"ok": true, ...}`. A refusal is `{"ok": false, "error": <code>, "message": <one sentence the model can explain to the caller>}`.
- Every call logs `tool_call` and `tool_result` (§8).
- Dates and times in results are given twice: ISO 8601 with offset (`2026-11-10T09:30:00+01:00`) and a spoken form in the call's language (`"martes 10 de noviembre a las nueve y media"`).
- IDs: patients `p_` + 4 digits, practitioners `d_` + slug, appointments `a_` + 8 hex, slots `s_` + 8 hex. Slot IDs are minted per call and mean nothing outside it.

### 5.2 What "this call looked up" means

The session keeps, for the current call only:

- `patients_seen`: every patient returned by `find_patient`, and patients registered in this call.
- `identified`: at most one patient, the one the call acts for (rule below).
- `slots_offered`: every slot returned by `search_availability`, with its practitioner, start, end and treatment.
- `appointments_seen`: every appointment returned by `list_appointments`.
- `triage`: the last `record_triage` answers, if any.
- `pending`: at most one staged change.

**Identification rule.** A patient becomes `identified` when `find_patient` returned exactly that one patient and either the date of birth given matched, or the caller-ID phone matched the patient's phone and the name given matched. A patient registered in this call is identified. A new `find_patient` that returns a different single match replaces the identified patient only if no change is pending. (Decision D7.)

### 5.3 The tools

Refusal codes are listed in §5.4.

| Tool | Parameters | Does | Refuses with |
|---|---|---|---|
| `find_patient` | `name?` string, `dob?` date (`YYYY-MM-DD`), `phone?` string | Up to 3 matches: `patient_id`, first name, first surname, `dob_matches` (bool, only if a dob was given), `phone_matches` (bool). Never returns a dob or phone the caller did not give. More than 3 matches → `too_many_matches` with a hint for what to ask. Sets `identified` per §5.2 | `bad_arguments` (no argument given) |
| `register_patient` | `first`, `last1`, `last2?`, `dob`, `phone` | **Stages** a new patient (pending); returns a provisional `patient_id` usable by `book` in this call. Saved together with the booking by `confirm_pending` | `lookup_first` (no `find_patient` in this call), `already_registered` (an exact name + dob match exists), `pending_exists`, `bad_phone`, `bad_dob` |
| `list_appointments` | `patient_id` | The patient's appointments from `session.now` on, status `booked` | `not_identified` |
| `search_availability` | `treatment` (§5.5), `practitioner_id?`, `after?` date, `before?` date, `part_of_day?` `morning`\|`afternoon` | Up to 5 slots, earliest first, plus `blocked`: one entry per practitioner who can do the treatment but has no slot in the range, with `reasons` from §5.6. Window: from tomorrow to 28 days ahead (`BOOKING_HORIZON_DAYS`) | `unknown_treatment`, `unknown_practitioner`, `bad_range`, `triage_required` (treatment `urgency` and no `record_triage` yet) |
| `book` | `slot_id`, `patient_id`, `treatment` | Stages a booking; returns a summary to read back | `not_identified`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `patient_overlap`, `pending_exists`, `triage_required` |
| `move` | `appointment_id`, `slot_id` | Stages a move to a slot for the same treatment | `appointment_not_seen`, `not_identified`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `appointment_started`, `pending_exists` |
| `cancel` | `appointment_id` | Stages a cancellation | `appointment_not_seen`, `not_identified`, `appointment_started`, `pending_exists` |
| `confirm_pending` | – | Saves the staged change (and a staged registration with it) in one transaction, with its operation key. A retry of the same change returns the first result with `already_saved: true` | `nothing_pending`, `no_caller_reply` (no caller transcript logged after the change was staged), `slot_taken` (lost a race; the change is discarded) |
| `discard_pending` | – | Drops the staged change (and a staged registration) | `nothing_pending` |
| `record_triage` | `pain` bool, `swelling` bool, `fever` bool, `breathing_difficulty` bool, `trauma` bool, `heavy_bleeding` bool | Stores the answers. If any red flag (§6.2) is true, code plays `emergency`, logs an escalation, and the result says so | `bad_arguments` |
| `take_message` | `name`, `phone`, `text` | Saves a callback message at once (not staged) | `bad_phone`, `bad_arguments` |
| `record_no_action` | `reason` (§5.7) | Records why the call changed nothing. Can be called again; the last one wins | `unknown_reason`, `change_saved` (a change was already saved in this call) |
| `clinic_info` | `topic` `hours`\|`location`\|`prices`\|`insurance`\|`treatments`\|`payment`\|`contact` | Catalogue text for the topic, nothing else | `unknown_topic` |
| `escalate` | `reason` (§5.8), `urgency` `emergency`\|`urgent`\|`routine` | Logs an escalation. `urgency: emergency` makes code play `emergency` (once per call) | `unknown_reason`, `bad_arguments` |

Rules that apply across tools:

- **Same day.** A slot whose start date is `session.now.date()` is never offered and never accepted (`same_day`). This includes urgency. (Decision D1.)
- **One pending change at a time.** A second staging call while one is pending returns `pending_exists`; the model must confirm or discard first.
- **Re-check on save.** `confirm_pending` re-checks the slot inside the write transaction. The database's overlap guard (§10) is the last line, not the first.
- **A staged change that is never confirmed is never saved.** At `finish()`, any pending change is discarded and logged as `discarded` with `by: "finish"`.
- **Confirmation needs a caller turn.** `confirm_pending` needs at least one caller transcript logged after the change was staged. This does not prove the caller said yes; it stops the model from confirming in the same breath as staging. The eval judge checks the yes itself.

### 5.4 Error codes

`bad_arguments`, `too_many_matches`, `lookup_first`, `already_registered`, `bad_phone`, `bad_dob`, `not_identified`, `unknown_treatment`, `unknown_practitioner`, `bad_range`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `patient_overlap`, `pending_exists`, `triage_required`, `appointment_not_seen`, `appointment_started`, `nothing_pending`, `no_caller_reply`, `change_saved`, `unknown_reason`, `unknown_topic`, `store_unavailable`.

`store_unavailable` is returned when the database cannot be written (locked past its busy timeout, disk error). Nothing is saved; the pending change stays pending, so the model can retry `confirm_pending` once.

### 5.5 Treatments

`checkup`, `cleaning`, `filling`, `extraction`, `ortho_consult`, `urgency`. Durations and who can do each come from the catalogue (EN-01), in multiples of 15 minutes.

### 5.6 Blocking reasons (`search_availability.blocked[].reasons`)

`not_working_that_day`, `absent`, `clinic_closed`, `fully_booked`, `outside_part_of_day`, `patient_busy` (the patient has an appointment at that time), `beyond_horizon`.

Each reason may carry `dates` (the dates it applies to) and `until` (for absences).

### 5.7 No-action reasons

`info_only`, `message_taken`, `no_suitable_slot`, `caller_declined`, `not_identified`, `out_of_scope`, `emergency_referred`, `technical_failure`, `caller_hung_up`.

The first six are from PLAN.md. `emergency_referred`, `technical_failure` and `caller_hung_up` are added (Decision D9).

**Fallback at the end of a call.** If nothing was saved and no reason was recorded, `finish()` derives one from what happened, in this order, and logs it with `derived: true`:

1. A silence-guard failure or provider error ended the call → `technical_failure`
2. An emergency escalation → `emergency_referred`
3. A change was discarded at `finish()` → `caller_hung_up`
4. A message was taken → `message_taken`
5. `find_patient` was called and no one was identified → `not_identified`
6. A search returned no slots → `no_suitable_slot`
7. `clinic_info` was called → `info_only`
8. Otherwise → `out_of_scope`

### 5.8 Escalation reasons

`red_flag_breathing`, `red_flag_swelling_fever`, `red_flag_trauma`, `red_flag_bleeding`, `medical_advice_request`, `wants_human`, `complaint`, `other`.

---

## 6. Fixed phrases

### 6.1 The catalogue

Prerecorded clips, one per phrase and language, at `backend/app/voice/clips/<id>.<lang>.wav` (mono, 16-bit, 24 kHz). They are generated once from the text below with the local Kokoro voice (`make clips`) and committed, so they play even when every provider is down. The text in this table is the source; change it here and regenerate.

| ID | When | Spanish | English |
|---|---|---|---|
| `emergency` | Red flag detected (§6.2) | Por lo que me cuenta, necesita atención urgente. Llame ahora al uno uno dos o vaya a urgencias del hospital más cercano. | From what you describe, you need urgent care. Please call one one two now, or go to the nearest hospital emergency department. |
| `recover` | Silence guard, first step (§7) | Perdone, un segundo. | Sorry, one moment. |
| `hold` | Brain still working after `HOLD_PHRASE_SECS` | Un momento, lo estoy mirando. | One moment, I'm checking. |
| `still_there` | Dead air after the agent spoke (§7.4) | ¿Sigue ahí? | Are you still there? |
| `technical_close` | Silence guard failed, provider error, brain down for good | Lo siento, tenemos un problema técnico. Por favor, vuelva a llamar en unos minutos. Hasta luego. | I'm sorry, we're having a technical problem. Please call again in a few minutes. Goodbye. |
| `cap_close` | Call cap reached | Tenemos que terminar la llamada. Si necesita algo más, vuelva a llamar. Gracias, hasta luego. | We need to end the call now. If you need anything else, please call again. Thank you, goodbye. |
| `idle_close` | Dead air after the last `still_there` | Como no le oigo, voy a colgar. Vuelva a llamar cuando quiera. Hasta luego. | I can't hear you, so I'll hang up now. Please call again any time. Goodbye. |

The language is `session.language` (`es` or `en`, default `es`), set from the caller's transcripts by the voice layer: the speech-to-text language tag in the cascade, the `language` of the last `ask_clinic` in the speech-to-speech layers. Every change is logged as `language_changed`.

No phrase may contain a word that could be heard as agreement ("sí", "vale", "de acuerdo", "yes", "okay"), so a phrase can never be taken for a yes to a staged change.

### 6.2 Emergencies: guaranteed by code

The speech-to-speech model handled a toothache in FD-01 without ever mentioning 112. So the emergency instruction is not left to any model. Three triggers, any one of which plays `emergency`:

1. **The brain's triage:** `record_triage` with `breathing_difficulty`, `trauma` or `heavy_bleeding` true, or `swelling` and `fever` both true.
2. **The brain's escalation:** `escalate` with `urgency: emergency`.
3. **A transcript matcher in code**, run on every final caller transcript, independent of both models. It matches fixed phrase lists per language (`app/redflags.py`, VO-01) for trouble breathing, swelling with fever, a blow or accident to the face or mouth, and bleeding that will not stop. It is tuned to miss nothing in the eval scenarios; a false alarm costs one unnecessary sentence. (Decision D2.)

What happens, whichever trigger fires first:

- `CallControl.play("emergency")`, then an `escalation` event (`reason` from §5.8, `urgency: emergency`, `trigger: triage | escalate | matcher`) and an `emergency` flag on the outcome.
- **Once per call.** Later triggers log `escalation` again with `repeat: true` but do not replay the clip, unless the caller asks again what to do.
- The call goes on afterwards. The agent may take a message or book an urgency slot if the caller asks for one, but must not suggest that a dental appointment replaces emergency care. (Decision D3.)

**Pain without a red flag.** Any `urgency` search needs `record_triage` first (`triage_required`). That makes the brain ask the triage questions before offering an urgency slot, whichever voice is active.

The brain and voice prompts still tell the models about red flags. The code path is the guarantee; the prompts make the conversation around it sound right.

---

## 7. Silence guard and dead air

All of it lives in the shared plumbing, as pure logic fed with events and the timing clock (unit-testable without audio). It reads caller transcripts, not loudness, so line noise is never caller speech. It ignores every instruction the caller gives the model: telling the agent "be quiet" or "shut up" cannot disable it.

### 7.1 Response guard

FD-01 measured three failures with Gemini Live: silences of 8 to 12 s after the caller finished, caller turns skipped entirely, and an agent that stayed silent for the rest of the call after being told to shut up. The response guard covers all three.

**It arms** when a final caller transcript arrives and the agent is not speaking at that moment. The deadline is `RESPONSE_TIMEOUT_SECS` (default 5.0 s) after the transcript arrives.

**It disarms** when any agent audio leaves the server (model speech or a fixed phrase), or when new caller speech starts. New caller speech re-arms it from the next final caller transcript. A caller turn with no agent audio before the next caller turn counts as **skipped** and is logged as such; the guard keeps running on the latest turn.

**It is held** while `CallControl.busy()` is open (brain or tool working), up to `BUSY_MAX_SECS` (default 15 s); after that the hold no longer counts and the deadline applies. When the hold ends, the guard re-arms with a fresh deadline, so the answer the brain just produced has time to start before a recovery could talk over it.

**When the deadline passes** (`silence_guard_fired`, with the turn and how long it waited):

1. Play `recover`, then `nudge()` the voice model to answer the last caller turn.
2. If no agent audio starts within `RESPONSE_TIMEOUT_SECS` of the nudge, that is a **failed recovery**.

A recovery succeeds when model audio starts; that is logged as `silence_guard_recovered` with the extra wait.

### 7.2 "Be quiet" must never mute the call

The guard arms after every caller turn, including "cállate", "shut up" or "be quiet". So the agent always gets the chance to answer something short ("Claro, aquí sigo"), and if it stays silent the guard recovers as above. A caller who wants silence and then stays silent meets the dead-air prompts (§7.4) instead, which end the call politely. There is no mode in which the agent stays mute and the call stays open.

### 7.3 When recovery fails too

- **First failed recovery** in a call: `play("recover")` again and `nudge()` once more. For the speech-to-speech layers, the layer may first reopen the model session seeded with the transcript so far, if it supports that (phase 2; not required for the MVP).
- **Second failed recovery** in a row, or a total of `GUARD_MAX_PER_CALL` (default 4) guard firings in one call: the voice is treated as unresponsive. `CallControl.end("technical_close")`, log `voice_unresponsive`, add the `voice_unresponsive` flag to the outcome. Any pending change is discarded; the derived no-action reason is `technical_failure`. The console lists these calls. (Decision D5.)
- A recovery that succeeds resets the in-a-row count, not the per-call total.

### 7.4 Dead air after the agent spoke

Separate from the response guard. When the agent's audio has ended and no caller transcript arrives for `IDLE_PROMPT_SECS` (default 10 s), and nothing is busy: play `still_there`. Up to `IDLE_PROMPT_MAX` (default 2) in a row; any caller transcript resets the count. After the last one, another `IDLE_PROMPT_SECS` of silence → `end("idle_close")`.

### 7.5 Other endings

- **Call cap:** at `MAX_CALL_SECS` (default 300 s) → `end("cap_close")`, log `call_cap_reached`. Built today without the closing clip.
- **Goodbye:** when the agent's transcript closes the call (a goodbye after a saved change, a recorded no-action reason, or a taken message), the plumbing ends the call after `HANGUP_GRACE_SECS` (default 3 s). Any caller transcript in the grace window that is not itself a goodbye cancels the hang-up. A busy tool holds it until the tool returns. (VO-03.)
- **Provider quota or credit errors:** `end("technical_close")`, log `provider_error` with `category: quota`, flag the outcome `provider_quota`.

### 7.6 Settings

| Env var | Default | Meaning |
|---|---|---|
| `RESPONSE_TIMEOUT_SECS` | 5.0 | Caller transcript → agent audio, before the guard fires |
| `BUSY_MAX_SECS` | 15 | Longest a busy brain or tool holds the guard |
| `GUARD_MAX_PER_CALL` | 4 | Guard firings in one call before the voice counts as unresponsive |
| `HOLD_PHRASE_SECS` | 2.5 | Brain still working → play `hold` once |
| `BRAIN_TIMEOUT_SECS` | 12 | Brain gives up on one request |
| `IDLE_PROMPT_SECS` | 10 | Dead air after the agent spoke → `still_there` |
| `IDLE_PROMPT_MAX` | 2 | `still_there` prompts in a row before `idle_close` |
| `HANGUP_GRACE_SECS` | 3 | Goodbye → hang up |
| `MAX_CALL_SECS` | 300 | Call cap (built) |

The 5 s default sits above the cascade's p90 heard latency (3.3 s) and well below the 8-12 s silences it must catch.

---

## 8. Call log (JSONL)

One file per call: `data/calls/<call_id>.jsonl` (`CALLS_DIR`), git-ignored. `call_id` is 12 hex characters. One JSON object per line, appended as things happen:

```json
{"t": 12.345, "kind": "transcript", "role": "caller", "text": "..."}
```

- `t`: seconds on the timing clock since the call started (3 decimals). Always present.
- `kind`: from the list below. Readers must ignore kinds and fields they do not know.
- `call_started` carries `schema: 1`. A change that breaks readers bumps it.

The log is private: it holds transcripts and patient data. Only the console API (localhost) and the public review link (§11, redacted) read it.

| kind | Fields | Status |
|---|---|---|
| `call_started` | `schema`, `now` (call clock, ISO), `monotonic`, `voice`, `channel` (`browser`\|`bench`\|`eval`\|`phone`), `caller_phone?` | Built (no `schema` yet) |
| `voice_config` | `voice` plus the layer's models and voice names | Built |
| `transcript` | `role` `caller`\|`agent`, `text`, `interrupted?` (agent), `language?` | Built |
| `language_changed` | `language` | To build |
| `greeting_latency` | `secs` | Built |
| `latency` | `secs` (server: caller stopped → first agent audio out) | Built |
| `latency_breakdown` | `measured_from`, `total_secs`, `stages[]` | Built |
| `heard_latency` | `secs` (measured by the browser page) | Built |
| `usage` | `service` `llm`\|`s2s`\|`stt`\|`tts`\|`brain`, `model`, token or character counts | Built (`brain`, `stt` to build) |
| `brain_request` / `brain_reply` | `request`, `language` / `say`, `phrase`, `secs`, `error?` | To build |
| `tool_call` / `tool_result` | `id`, `name`, `args` / `id`, `ok`, `result` or `error`, `message` | To build |
| `staged` | `change` (`register`\|`book`\|`move`\|`cancel`), `details`, `operation_key` | To build |
| `saved` | `change`, `operation_key`, `change_id`, `already_saved` | To build |
| `discarded` | `change`, `operation_key`, `by` (`tool`\|`finish`\|`race`) | To build |
| `triage` | the six answers, `red_flags[]` | To build |
| `escalation` | `reason`, `urgency`, `trigger`, `repeat?` | To build |
| `message_taken` | `message_id` | To build |
| `phrase_played` | `phrase`, `language`, `secs` | To build |
| `silence_guard_fired` | `turn`, `waited_secs`, `attempt` | To build |
| `silence_guard_recovered` | `turn`, `extra_secs` | To build |
| `turn_skipped` | `turn` | To build |
| `voice_unresponsive` | `guard_firings` | To build |
| `dead_air_prompt` | `count` | To build |
| `provider_error` | `provider`, `category` `quota`\|`auth`\|`unavailable`\|`other`, `detail` | To build |
| `voice_error` / `voice_unavailable` | `error` / `voice` | Built |
| `caller_hung_up` / `call_cap_reached` / `agent_hung_up` | – / `secs` / `phrase?` | Built / built / to build |
| `recording_saved` | `path`, `secs`, `rate` | To build (VO-04) |
| `audio_metrics` | §9.3 | To build (VO-04) |
| `outcome` | `result` `changed`\|`no_action`, `changes[]` (change IDs), `no_action_reason?`, `derived`, `escalations[]`, `flags[]` | To build |
| `review_link_created` | `expires_at` (never the token) | To build (CO-05) |
| `call_ended` | `duration_secs` | Built |

`outcome` is written exactly once, by `finish()`, before `call_ended`. Its `flags` come from this list: `emergency`, `provider_quota`, `voice_unresponsive`, `call_cap`, `brain_error`. The console's "quota" filter reads `provider_quota`.

---

## 9. Recording

### 9.1 Calls (to build, VO-04)

- **Where:** `recordings/<call_id>.wav` at the repository root, git-ignored, next to `recordings/<call_id>.timing.json`.
- **Format:** WAV, 2 channels, 16-bit PCM, 24 kHz. **Left: the caller**, as the server received it. **Right: the agent**, as the server sent it, fixed phrases included.
- **One shared timeline:** sample `n` is at `t = n / 24000` on the timing clock, the same `t` as the call log. Sample 0 is `t = 0` (`call_started`). Where a side sent nothing, its channel is silence. So `t` from any log event is a position in the recording, which is how the console jumps to a message.
- The recorder taps the transport, below the voice layer, so it is the same for every `VOICE`.
- `timing.json`: `{"rate": 24000, "caller": [[start, end], ...], "agent": [[start, end], ...], "underruns": [t, ...]}`, speech segments in seconds on the same timeline.

### 9.2 Bench recordings (built)

`scripts/voice_bench.py` saves `recordings/bench/<voice>/<call_id>.wav` in the same channel layout and rate, but on the **caller's** clock (each frame placed when it left or reached the scripted caller), because it measures what the caller hears. Do not mix the two timelines when comparing.

### 9.3 Audio metrics (`audio_metrics` event, after the call)

`greeting_secs`, `turns`, `answered_turns`, `overlapped_turns`, `overlap_rate`, `latency_p50_ms`, `latency_p90_ms`, `skipped_turns`, `guard_firings`, `dead_air_secs` (total gaps over 3 s with nobody speaking), `barge_ins`, `yield_p50_ms` (caller starts over agent → agent audio stops), `underruns`.

---

## 10. Database

SQLite in WAL mode, `data/clinic.db`, git-ignored, rebuilt by `make seed`. `PRAGMA foreign_keys = ON`, `busy_timeout = 5000`. All writes go through `engine/store.py` in a `BEGIN IMMEDIATE` transaction.

Times are stored as UTC text `YYYY-MM-DDTHH:MM:SSZ` (fixed width, so text order is time order) and converted to `Europe/Madrid` at the edges. Dates are `YYYY-MM-DD` in the clinic's zone.

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- seed version, seed date
CREATE TABLE site (site_id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
                   phone TEXT NOT NULL, timezone TEXT NOT NULL);
CREATE TABLE practitioners (practitioner_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                   role TEXT NOT NULL CHECK (role IN ('dentist','hygienist')),
                   languages TEXT NOT NULL);                     -- e.g. 'es,en'
CREATE TABLE treatments (treatment_id TEXT PRIMARY KEY,          -- §5.5
                   name_es TEXT NOT NULL, name_en TEXT NOT NULL,
                   minutes INTEGER NOT NULL CHECK (minutes > 0 AND minutes % 15 = 0),
                   price_eur INTEGER);                           -- NULL = "ask at the clinic"
CREATE TABLE practitioner_treatments (practitioner_id TEXT REFERENCES practitioners,
                   treatment_id TEXT REFERENCES treatments, PRIMARY KEY (practitioner_id, treatment_id));
CREATE TABLE working_hours (practitioner_id TEXT REFERENCES practitioners,
                   weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0 = Monday
                   start_local TEXT NOT NULL, end_local TEXT NOT NULL);       -- 'HH:MM'
CREATE TABLE closures (date TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE absences (practitioner_id TEXT REFERENCES practitioners,
                   start_date TEXT NOT NULL, end_date TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT NOT NULL,
                   last_name1 TEXT NOT NULL, last_name2 TEXT, dob TEXT NOT NULL,
                   phone TEXT NOT NULL,                          -- E.164
                   language TEXT NOT NULL DEFAULT 'es', created_by_call TEXT);
CREATE TABLE appointments (appointment_id TEXT PRIMARY KEY,
                   patient_id TEXT NOT NULL REFERENCES patients,
                   practitioner_id TEXT NOT NULL REFERENCES practitioners,
                   treatment_id TEXT NOT NULL REFERENCES treatments,
                   start_utc TEXT NOT NULL, end_utc TEXT NOT NULL CHECK (end_utc > start_utc),
                   status TEXT NOT NULL CHECK (status IN ('booked','cancelled')),
                   created_by_call TEXT);
CREATE TABLE changes (change_id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
                   call_id TEXT NOT NULL, action TEXT NOT NULL
                   CHECK (action IN ('register','book','move','cancel','demo_reset')),
                   details TEXT NOT NULL, saved_utc TEXT NOT NULL);  -- details: JSON before/after
CREATE TABLE messages (message_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, name TEXT NOT NULL,
                   phone TEXT NOT NULL, text TEXT NOT NULL, saved_utc TEXT NOT NULL);
CREATE TABLE review_links (token_sha256 TEXT PRIMARY KEY, call_id TEXT NOT NULL UNIQUE,
                   expires_utc TEXT NOT NULL);
```

**No overlapping appointments, enforced by the database.** SQLite has no exclusion constraint, so two triggers, `BEFORE INSERT` and `BEFORE UPDATE OF start_utc, end_utc, practitioner_id, status ON appointments`, `RAISE(ABORT, 'overlap')` when the new or changed row is `booked` and another `booked` row for the same practitioner, or the same patient, has `start_utc < NEW.end_utc AND end_utc > NEW.start_utc`. `BEGIN IMMEDIATE` serialises writers, so the trigger's check and the write cannot interleave. The store turns `overlap` into `slot_taken` or `patient_overlap`. EN-03 tests two concurrent bookings of one slot: exactly one succeeds.

**Operation keys.** Each staged change gets `operation_key = "<call_id>:<n>"`, where `n` counts the changes staged in the call (1, 2, ...). The key and the write go in one transaction. A second save with a key already in `changes` writes nothing and returns the first result. A move is one `UPDATE` of the appointment; a cancel sets `status = 'cancelled'`. Neither deletes rows.

---

## 11. Review link (CO-05)

- When a call ends, the session creates a token with `secrets.token_urlsafe(32)` (256 random bits). The link is `/r/<token>` on the public call server.
- Only `sha256(token)` is stored (`review_links`), with an expiry of `REVIEW_LINK_DAYS` (default 7) days. The token itself is shown once, to the caller's browser, at hang-up. It never appears in the log.
- The public route serves that one call's review: transcript, recording and tool steps, **redacted**: tool arguments and results show only the identified patient's own data; other patients returned by `find_patient` show as "another patient". (Decision D10.)
- An unknown, expired or malformed token returns 404, the same response in each case. No public route lists calls or accepts a `call_id`.

---

## 12. Eval scenarios (EV-01)

One YAML file per scenario: `backend/evals/scenarios/<id>.yaml`, where `id` is `<type>-<lang>-<nn>`, e.g. `book_known-es-03`.

```yaml
id: book_known-es-03
type: book_known        # new_patient | book_known | move | cancel | urgent | red_flag
                        # | info | message | adversarial | language_switch
language: es            # the caller's first language
now: "2026-11-09T10:15:00+01:00"   # the call clock
fixture: default        # the seeded database; overrides below apply on top
overrides: []           # e.g. [{absent: d_ortiz, from: 2026-11-10, to: 2026-11-12}]
caller_phone: "+34600000123"       # caller ID, or null
persona: >
  A retired teacher, polite, a bit hard of hearing, asks for things to be repeated.
facts:                  # what the caller knows and may say
  name: "..."
  dob: "1958-04-02"
goal: >
  A cleaning next week, any morning, with the same hygienist as last time.
max_turns: 16
expect:
  outcome: changed      # changed | no_action
  changes:
    - {action: book, treatment: cleaning, patient: p_0123, part_of_day: morning,
       after: "2026-11-16", before: "2026-11-21"}
  no_action_reason: null
  escalation: null      # or {reason: red_flag_breathing, urgency: emergency}
  phrases: []           # fixed phrases that must play, e.g. [emergency]
  forbidden_tools: []   # e.g. [book] for an info-only call
judge:                  # rubric items on top of the default ones
  - "Repeats the time slowly when asked"
```

Default checks on every scenario: database state equals `expect.changes` and nothing else; every save came after `confirm_pending`; no `slot_not_offered` was ever bypassed; a discarded change was never saved; a no-action call has a reason from §5.7; the agent stayed in the caller's language (following any switch); no medical advice; polite.

Text-only evals run the brain and tools with no voice layer: the simulated caller's lines are the caller transcripts, and the brain's `say` is the agent transcript. Fixed phrases are logged as `phrase_played` and appear in the agent transcript as their text.

---

## 13. Metrics: how numbers are reported

- **Every latency figure goes out with its overlap rate and its count.** The latency distribution leaves out overlapped turns (the agent's audio started before the caller finished, or less than 150 ms after), so a median alone can look better than the call felt: in one Gemini browser test 4 of 8 turns overlapped. Format: `p50 1490 / p90 1991 ms (n = 42 answered, overlap 9%, 1 unanswered)`.
- **Overlap rate** = overlapped turns / (answered + overlapped turns).
- **Unanswered turns** (the guard fired, or the turn was skipped) are reported as a count beside the latency, never folded into it.
- **Say which latency.** *Heard* latency is measured at the caller (bench or browser page); *server* latency is caller stopped → first agent audio out at the server. They differ by the network and the caller's audio path. Tables name which one they show.
- **Cost** is reported per call minute and per call, with the date of the prices assumed (`PRICES_AS_OF` in `scripts/call_costs.py`), and whether the run was billed (free-tier runs are priced at paid rates).
- Bench and browser reports (VO-06) and the README tables follow these rules.

---

## 14. Decisions for review

Each one is a real product or safety choice the plan left open. The default is what this file specifies; changing it is a small edit here plus a note on the affected task.

| # | Decision | Default here | Alternative | Why it matters |
|---|---|---|---|---|
| D1 | Same-day slots for urgent pain | Refused for every treatment, urgency included, as PLAN.md says | Allow same-day `urgency` slots with a lead time (e.g. 2 h) | A patient in pain hears "tomorrow at the earliest". Red flags still go to 112, but a clinic would usually see urgent pain the same day. Same-day booking also needs staff to see it in time |
| D2 | What the red-flag transcript matcher does | Plays `emergency` itself, accepting false alarms | Only force the brain to run triage, and let triage decide | Playing it directly guarantees the 112 instruction even if both models fail; the cost is an occasional unnecessary warning (e.g. a past accident mentioned in passing) |
| D3 | Booking after an emergency referral | Allowed only if the caller asks; the agent must not imply it replaces emergency care | End the call after the emergency phrase, or refuse bookings for the rest of the call | Ending the call is the safest message but leaves a caller who wants a follow-up with nothing |
| D4 | Silence guard timeout | 5.0 s | 3.5 s (catches more, but fires on slow cascade turns) or 8 s (fewer false fires, longer silences) | Too short talks over slow answers; too long leaves the caller in silence |
| D5 | When silence recovery fails twice | End the call with `technical_close` and flag it | Switch the call to the fallback voice layer mid-call, or keep the call open on dead-air prompts | Switching voices mid-call is the best experience but is phase-2 work; keeping a broken call open wastes the caller's time |
| D6 | Cascade architecture | The cascade's text model is the brain, with the tools directly | The same voice-plus-brain split as speech-to-speech | One hop fewer saves about a second on the slower option; the split would make the two options more alike to compare. Rules are in code either way |
| D7 | What identifies a patient | One match plus matching date of birth, or one match on caller-ID phone plus matching name | Name and date of birth always, even with caller ID | Caller ID can be spoofed or shared in a family; requiring the date of birth is safer but adds a question to every call |
| D8 | When a new patient is saved | Staged with the first booking, saved in one transaction by `confirm_pending`; the date of birth is required | Save the registration at once; or make the date of birth optional as in PLAN.md | Staging means a caller who changes their mind leaves no record. Without a date of birth the new patient cannot be identified on their next call |
| D9 | Lists and tools added to PLAN.md | No-action reasons `emergency_referred`, `technical_failure`, `caller_hung_up`; tools `record_triage` and `take_message` | Keep PLAN.md's six reasons and fold these into `out_of_scope`; take messages in the log only | Without them, a dropped call, an emergency or a broken voice all look like "out of scope" in the console, and messages for a callback have nowhere to go |
| D10 | What the public review link shows | Transcript, recording and redacted tool steps, for 7 days | Transcript and recording only; or a shorter expiry | Tool steps are the most impressive part of the demo; redaction keeps other patients' data out. Even synthetic demo calls carry the visitor's own voice |
| D11 | Confirmation check | `confirm_pending` needs a caller turn after staging; the judge checks the yes | Also require a yes-like word in that caller turn, matched in code | A word list in code catches the model confirming on a "no", but misfires on "sí, pero..."; the judge is slower but sees meaning |
