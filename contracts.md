# Contracts

The formats and rules every lane builds against. PLAN.md says what we build and why; this file says exactly how the parts fit together. When the two disagree, this file wins for interfaces and PLAN.md wins for scope.

**Changing this file.** After FD-02 is approved, any change needs a note on the board, on the task that needs it, saying what changed and which lanes it affects. Additive changes (a new optional field, a new event kind) are fine with a note. Renaming or removing something, or changing a meaning, needs the human's approval first.

**Status of each part.** "Built" means FD-01 code already does it and the code is the reference. "To build" means the task named next to it builds it against this file.

---

## 1. Principles that bind every lane

1. **No business rule depends on which voice layer is active.** Identification, slot validity, staging, confirmation, red flags, the silence guard, the call cap and the outcome all live in the session, the tools, the engine or the shared call plumbing (`app/voice/common.py`). A voice layer only moves audio and text, and hands clinic questions to the brain.
2. **Safety-critical speech is played by code, never generated.** The opening (which says the call is recorded and the caller is talking to an AI), the emergency instruction, silence recovery, quota and technical-failure messages are fixed phrases (§6), played as prerecorded clips. A model may say similar things in its own words, but the guarantee does not rely on it.
3. **Tools never raise.** Every tool returns a JSON object. A refusal is a normal result with an error code from a closed list and a message the model can say to the caller in its own words.
4. **Every call reads one clock** (§2). No code under `backend/engine/`, and no code in `app/session.py`, `app/tools.py`, `app/prompt.py` or `app/clinic.py`, reads the wall clock. The single exception is the one read in `CallSession.start()` described in §2.
5. **Closed vocabularies are closed.** Reason codes, topics, treatments, error codes and event kinds are listed here. A value outside the list is refused, never stored.

---

## 2. Clocks

There are two clocks, for two different jobs.

| Clock | What it is | Who reads it | Status |
|---|---|---|---|
| **Call clock** `session.now` | A timezone-aware `datetime` in the clinic's zone (`Europe/Madrid`), fixed when the call starts and constant for the whole call. Tests and evals pass it in: `CallSession.start(now=...)`. | Engine, session, tools, prompt, catalogue: anything about dates, "today", "tomorrow", same-day rules and the urgency lead time, the dated calendar | Built (fixed at start). To build: clinic zone instead of UTC (EN-04) |
| **Timing clock** | `time.monotonic()`, as seconds since the call started. It is the `t` of every log event (§8) and sample 0 of the recording (§9). | Call log, recorder, silence guard, dead-air prompts, call cap, metrics | Built |

Rules:

- **"Today"** is `session.now.date()` in the clinic's zone. A call that runs past midnight still uses the date it started on (the call cap keeps this to minutes).
- Engine functions take `now` as a parameter. They have no default that reads the wall clock.
- Only `CallSession.start()` may read the wall clock, and only when no `now` was passed. A test (`tests/test_no_wall_clock.py`, EN-04) scans the files in principle 4 for `datetime.now(`, `datetime.utcnow(`, `date.today(` and `time.time(`, and fails if any appear, except one line inside `CallSession.start()` marked with the comment `# wall-clock: call start`. The test also fails if that marker appears more than once or outside `start()`.
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

The choice of primary and fallback is provisional until VO-05 measures A2 in the same harness. FD-01's figures (scripted callers): heard latency p50/p90 1490/1991 ms for A1 (n = 46 answered turns), 2490/3281 ms for B (n = 28); about $0.027 per minute for A1 at paid rates, rising with call length, and $0.008 for B. These were recorded before the reporting rule in §13 and carry no overlap rate; VO-06 republishes them in that format, and until then they are not quoted elsewhere.

**Primary is a production intent, not the code default.** `config.VOICE` stays `cascade` by default until the Gemini paid tier is in place and VO-05 decides, because on the free tier Google may use the audio (scripted callers only). Automatic failover from primary to fallback is not in the MVP. (Decision D12.)

**Launch requirement: no free tier on public calls.** The public demo may go live only when every provider a public call reaches (speech-to-speech, speech-to-text, brain, text-to-speech) runs on terms under which the provider does not use call data to train or improve its models. A free tier whose terms allow that use is fine for scripted bench callers and must never serve a visitor. OP-02 checks this before launch and records, per provider, the plan and the terms relied on. (Decision D10.)

An unknown `VOICE` value is refused with the list of allowed values. Today that happens when the first call loads the layer; to build (VO-02): the call server checks `VOICE` at start-up and refuses to start.

### 3.2 The contract

Each layer exposes:

```python
async def run_call(transport: BaseTransport, session: CallSession) -> None
```

A layer must:

1. Let the plumbing play the `greeting` clip (§6) as soon as the transport connects, and not generate a greeting of its own. The clip tells the caller that the call is recorded and that they are talking to an AI, so that statement never depends on a model. Built today: the model speaks `session.greeting`, without the disclosure. To build (VO-03): the clip.
2. Use `session.instructions()` as its system prompt (the per-call prompt, VO-01).
3. Hand every clinic question to the brain (§4). The speech-to-speech layers get exactly one tool, `ask_clinic`; the cascade gets the clinic tools directly (§4.3).
4. Log both sides' speech as `transcript` events with `role` `"caller"` or `"agent"` (§8). Caller transcripts are required: red-flag detection, the silence guard and dead-air prompts all read them. Caller transcripts go through the plumbing, which adds the caller speech segment they belong to and the echo check (§7.7) before logging; a layer never sets `echo` itself.
5. Log what the call consumed as `usage` events.
6. Run through the shared plumbing in `app/voice/common.py` (`run_pipeline`). Built: transcripts, latency, usage and the bare call cap. To build (VO-02, VO-03, VO-04): the silence guard, dead-air prompts, fixed-phrase playback, provider-error handling and the recorder taps. A layer must not re-implement any of them.
7. Report provider errors through the shared plumbing, with a category (§8, `provider_error`).
8. Return when the caller hangs up, the call is cut, or the plumbing ends it. **Never call `session.finish()`**: the server calls it after `run_call` returns, also when the layer raised.

The `none` stub is exempt from 1 to 7: it says nothing, has no prompt, no brain, no guard and no recorder, and logs only the hang-up. It keeps 8. It exists for plumbing tests and must never serve a real caller.

### 3.3 Session fields every layer may read or set

| Field | Type | Set by | Rule |
|---|---|---|---|
| `session.now` | aware `datetime` | `CallSession.start()` | §2. Never changes during the call |
| `session.greeting` | str | session | Set by `CallSession.start()` from the `greeting.<lang>` entry of the active clip set's `clips/<voice_set>/manifest.json` (§6.1; the `CLIPS_VOICE_SET` set when that override is on), in the starting language. The plumbing logs it as an agent `transcript` when it plays the `greeting` clip |
| `session.language` | `"es"` \| `"en"` | call server at start; then the voice layer, through `session.set_language(lang)` | Starts as the language of the page the call came from (`es` if none), so the opening disclosure is in a language the visitor reads. The cascade sets it from the speech-to-text language tag of each final caller transcript; the speech-to-speech layers from the `language` of each `ask_clinic`. A change is logged as `language_changed`. Tools, the brain and fixed phrases read it; nothing else writes it |

### 3.4 What the shared plumbing gives every layer (to build in VO-02 and VO-03)

```python
class CallControl:
    async def play(self, phrase_id: str) -> None   # §6; agent channel; holds model audio until the clip ends
    async def nudge(self) -> None                   # ask the voice model to answer the last caller turn now
    async def end(self, phrase_id: str | None) -> None  # optional closing clip, then hang up
    def busy(self, reason: str) -> ContextManager   # brain or tool working: holds the silence guard (§7)
```

- `play` is the only way fixed phrases reach the caller. While a clip plays, audio from the voice model is held, then released when the clip ends, so the two never overlap. **Barge-in:** `greeting`, `emergency` and `technical_close` always play to the end; caller speech during them is still transcribed and handled once the clip ends (after `technical_close`, the call ends as planned). Every other clip stops when caller speech starts, like model audio, and its `phrase_played` event carries `interrupted: true`; an interrupted closing clip (`cap_close`, `idle_close`) still ends the call. (Decision D22.) After the clip, the voice model's context gets a short system note saying what was said (for example "The caller was told to call 112"), so it does not contradict or repeat it.
- `nudge` triggers a new response from the model on the conversation so far: `LLMRunFrame` for the cascade, a response request for the speech-to-speech layers. A layer that cannot nudge must say so with `supports_nudge = False`; the silence guard then plays `recover` without a nudge, and each such firing counts as a failed recovery at once (§7.3).

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
| Receptionist rules, dated calendar, catalogue | – | yes | built per call (VO-01) |
| Identification, slot validity, staging, confirmation | – | asked to follow them | enforced (§5) |
| Urgent pain triage questions | – | yes | triage required before an urgency slot (§5) |
| Red flags → 112 | may mention it | yes | **guaranteed** (§6.2) |

### 4.3 The cascade

In the cascade the text model already reads the caller's words, so it **is** the brain: it gets the clinic tools (§5) directly. There is no second hop. Every rule in §5, §6 and §7 applies unchanged, because they are in code. (Decision D6.)

**Same brain everywhere.** The cascade uses the same brain prompt (built by VO-01) and the same `tools_for_session` / `call_tool` as the brain behind `ask_clinic`, with one addition only: a block of spoken-reply rules appended at the end. It must not fork the prompt or the tool list. So the text evals (§12), which run that brain and those tools, test the decisions of both voice options.

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

- `patients_seen`: the patient `find_patient` identified (the only one whose data it returns, §5.3), and patients registered in this call.
- `dob_failures`: how many `find_patient` calls in this call matched a name but no date of birth (§5.3).
- `last_caller_text`: the latest final caller transcript (§7.1) and when it was logged, read by `confirm_pending`. `session.log()` updates it whenever it writes a `transcript` event with `role: "caller"` and without `echo: true` (§7.7), so every voice layer and the text evals keep it current without extra code.
- `identified`: at most one patient, the one the call acts for (rule below).
- `slots_offered`: every slot returned by `search_availability`, with its practitioner, start, end and treatment.
- `appointments_seen`: every appointment returned by `list_appointments`.
- `triage`: the last `record_triage` answers, if any.
- `pending`: at most one staged change.

**Identification rule.** A patient is always identified by name plus date of birth, and by nothing else. A patient becomes `identified` when, among the patients whose name matches (§5.10), exactly one has that date of birth. A patient registered in this call is identified. A later `find_patient` that identifies a different patient replaces the identified one only if no change is pending. Caller ID is not used, logged or passed to the prompt; using it is a phase 2 topic. (Decision D7.)

### 5.3 The tools

Refusal codes are listed in §5.4.

| Tool | Parameters | Does | Refuses with |
|---|---|---|---|
| `find_patient` | `name` string, `dob` date (`YYYY-MM-DD`), both required | Matches the name per §5.10. If exactly one name match has that date of birth: `identified: {patient_id, first_name, last_name1}`, and that patient becomes `identified` (§5.2). Otherwise `identified: null`, `name_matches` (0 to 3, or 4 meaning "4 or more") and a `hint`: `check_details` (no name match, or no match with that date of birth) or `ask_second_surname` (several matches with that date of birth). Patient data is returned only for the identified match: never another patient's name, id, date of birth or phone. A call where the name matched someone but the date of birth matched no one counts as a failed attempt (`dob_failures`); after `FIND_PATIENT_MAX_DOB_FAILURES` (default 3) failed attempts, every later `find_patient` in the call is refused, and the agent offers to take a message. (Decision D16.) | `bad_arguments` (a field missing), `bad_dob`, `too_many_dob_attempts` |
| `register_patient` | `first`, `last1`, `last2?`, `dob`, `phone` | **Stages** a new patient as the pending change; returns a provisional `patient_id` (`p_new1`) usable by `book` in this call, and identifies that patient. A `book` for it **extends the same pending change** to `register_book` (§10) instead of staging a second one. `confirm_pending` saves registration-only or registration plus booking, in one transaction | `lookup_first` (no `find_patient` in this call), `already_registered` (an exact name + dob match exists), `pending_exists`, `bad_phone`, `bad_dob` |
| `list_appointments` | `patient_id` | The patient's appointments from `session.now` on, status `booked` | `not_identified`, `wrong_patient` |
| `search_availability` | `treatment` (§5.5), `practitioner_id?`, `after?` date, `before?` date, `part_of_day?` `morning`\|`afternoon` | Up to 5 slots, earliest first, plus `blocked`: one entry per practitioner who can do the treatment but has no slot in the range, with `reasons` from §5.6. Window: from tomorrow to 28 days ahead (`BOOKING_HORIZON_DAYS`); for `urgency` only, it also includes today's slots that start at least `URGENCY_MIN_LEAD_MINUTES` after `session.now` (same-day rule below). Each slot carries `same_day` (bool) | `unknown_treatment`, `unknown_practitioner`, `bad_range`, `triage_required` (treatment `urgency` and no `record_triage` yet) |
| `book` | `slot_id`, `patient_id`, `treatment` | Stages a booking; returns a summary to read back | `not_identified`, `wrong_patient`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `too_soon`, `patient_overlap`, `pending_exists`, `triage_required` |
| `move` | `appointment_id`, `slot_id` | Stages a move to a slot for the same treatment | `appointment_not_seen`, `not_identified`, `wrong_patient`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `too_soon`, `appointment_started`, `pending_exists` |
| `cancel` | `appointment_id` | Stages a cancellation | `appointment_not_seen`, `not_identified`, `wrong_patient`, `appointment_started`, `pending_exists` |
| `confirm_pending` | – | Saves the staged change (and a staged registration with it) in one transaction, with its operation key. A retry of the same change returns the first result with `already_saved: true` | `nothing_pending`, `no_caller_reply` (no non-echo caller transcript logged after the change was staged, after the wait in §5.11), `caller_said_no` (the latest final caller turn contains a clear negative, §5.9; the change stays pending, except on the second refusal in a row, which discards it and returns `discarded: true`), `slot_taken` (lost a race; the change is discarded) |
| `discard_pending` | – | Drops the staged change (and a staged registration) | `nothing_pending` |
| `record_triage` | `pain` bool, `swelling` bool, `fever` bool, `breathing_difficulty` bool, `trauma` bool, `heavy_bleeding` bool | Stores the answers. If any red flag (§6.2) is true, code plays `emergency`, logs an escalation, and the result says so | `bad_arguments` |
| `take_message` | `name`, `phone`, `text`, `urgent?` bool (default false) | Saves a callback message at once (not staged). `urgent: true` marks it for staff as an urgent-pain callback (same-day rule below) | `bad_phone`, `bad_arguments` |
| `record_no_action` | `reason` (§5.7) | Records why the call changed nothing. Can be called again; the last one wins | `unknown_reason`, `change_saved` (a change was already saved in this call) |
| `clinic_info` | `topic` `hours`\|`location`\|`prices`\|`insurance`\|`treatments`\|`payment`\|`contact` | Catalogue text for the topic, nothing else | `unknown_topic` |
| `escalate` | `reason` (§5.8), `urgency` `emergency`\|`urgent`\|`routine` | Logs an escalation. `urgency: emergency` makes code play `emergency` (playback limits in §6.2) | `unknown_reason`, `bad_arguments` |

Rules that apply across tools:

- **Same day.** (Decision D1.)
  - For every treatment except `urgency`, a slot whose start date is `session.now.date()` is never offered and never accepted (`same_day`).
  - For `urgency`, a same-day slot is offered and accepted only if it starts at least `URGENCY_MIN_LEAD_MINUTES` (default 120) after `session.now`; an earlier one is refused with `too_soon`. The lead time is measured from the call clock, never the wall clock, so tests and evals give the same answer every run. Because `session.now` is fixed at the start, a call that lasts a few minutes measures the lead from its start; the call cap keeps the difference under 5 minutes.
  - A saved same-day urgency booking is flagged for staff: the appointment row has `same_day_urgent = 1` (§10), the call's outcome carries the `same_day_urgent` flag (§8), and the console shows both (CO-01, CO-02, CO-04).
  - If an urgency search finds no slot today, the agent offers a callback and takes it with `take_message(..., urgent: true)`; the outcome carries the `urgent_callback` flag. It may also offer the earliest later urgency slot if the caller prefers one.
- **One pending change at a time.** A second staging call while one is pending returns `pending_exists`; the model must confirm or discard first. The only exception is `book` for a patient registered in the pending change, which extends it (see `register_patient`). Discarding drops the whole pending change, registration included.
- **The call acts for one patient.** Every tool that takes a `patient_id` (`list_appointments`, `book`) refuses any id other than `identified` with `wrong_patient`; `move` and `cancel` refuse an appointment that does not belong to `identified` with the same code.
- **Re-check on save.** `confirm_pending` re-checks the slot inside the write transaction. The database's overlap guard (§10) is the last line, not the first.
- **A staged change that is never confirmed is never saved.** At `finish()`, any pending change is discarded and logged as `discarded` with `by: "finish"`.
- **Confirmation needs a caller turn, and not a "no".** `confirm_pending` needs at least one caller transcript logged after the change was staged (`no_caller_reply`), and is refused with `caller_said_no` if the latest final caller turn contains a clear negative (§5.9). Neither check proves the caller said yes: the first stops the model from confirming in the same breath as staging, the second from confirming on a refusal or a "wait". The eval judge checks the yes itself. (Decision D11.) Echo transcripts (§7.7) count for neither check.
- **Read-back is checked, not enforced.** When `confirm_pending` runs, it compares the staged change's date and time with what the agent said since staging (§5.12) and logs any mismatch. It never refuses on it in the MVP. (Decision D17.)

### 5.4 Error codes

`bad_arguments`, `too_many_dob_attempts`, `lookup_first`, `already_registered`, `bad_phone`, `bad_dob`, `not_identified`, `wrong_patient`, `unknown_treatment`, `unknown_practitioner`, `bad_range`, `slot_not_offered`, `treatment_mismatch`, `slot_taken`, `same_day`, `too_soon`, `patient_overlap`, `pending_exists`, `triage_required`, `appointment_not_seen`, `appointment_started`, `nothing_pending`, `no_caller_reply`, `caller_said_no`, `change_saved`, `unknown_reason`, `unknown_topic`, `store_unavailable`.

`store_unavailable` is returned when the database cannot be written (locked past its busy timeout, disk error). Nothing is saved; the pending change stays pending, so the model can retry `confirm_pending` once.

### 5.5 Treatments

`checkup`, `cleaning`, `filling`, `extraction`, `ortho_consult`, `urgency`. Durations and who can do each come from the catalogue (EN-01), in multiples of 15 minutes.

### 5.6 Blocking reasons (`search_availability.blocked[].reasons`)

`not_working_that_day`, `absent`, `clinic_closed`, `fully_booked`, `outside_part_of_day`, `patient_busy` (the patient has an appointment at that time), `beyond_horizon`, `within_lead_time` (`urgency` only: today's remaining free time starts less than `URGENCY_MIN_LEAD_MINUTES` from now).

Each reason may carry `dates` (the dates it applies to) and `until` (for absences).

### 5.7 No-action reasons

`info_only`, `message_taken`, `no_suitable_slot`, `caller_declined`, `not_identified`, `out_of_scope`, `emergency_referred`, `technical_failure`, `caller_hung_up`.

The first six are from PLAN.md. `emergency_referred`, `technical_failure` and `caller_hung_up` are added (Decision D9).

**Fallback at the end of a call.** If nothing was saved and no reason was recorded, `finish()` derives one from what happened, in this order, and logs it with `derived: true`:

1. A silence-guard failure or provider error ended the call → `technical_failure`
2. An emergency escalation → `emergency_referred`
3. A message was taken → `message_taken`
4. A change was discarded at `finish()` → `caller_hung_up`
5. A change was discarded after two refusals in a row (§5.9) → `caller_declined`
6. `find_patient` was called and no one was identified → `not_identified`
7. A search returned no slots → `no_suitable_slot`
8. `clinic_info` was called → `info_only`
9. Otherwise → `out_of_scope`

### 5.8 Escalation reasons

`red_flag_breathing`, `red_flag_swelling_fever`, `red_flag_trauma`, `red_flag_bleeding`, `medical_advice_request`, `wants_human`, `complaint`, `other`.

### 5.9 Clear negatives (`caller_said_no`)

`confirm_pending` checks the latest final caller transcript against one bilingual list, in both languages whatever `session.language` is (a caller may switch mid-sentence). The list lives in one module, `app/negatives.py` (EN-04):

- **Spanish:** `no`, `mejor no`, `todavía no`, `aún no`, `espera`, `espere`, `un momento`, `cancela`, `cancele`, `cancelar`, `olvídalo`, `olvídelo`, `déjalo`, `déjelo`.
- **English:** `no`, `nope`, `not yet`, `wait`, `hold on`, `cancel`, `don't`, `do not`, `never mind`, `stop`.

Matching: lowercase the text, strip accents (so `aun no` matches `aún no`), turn every character that is not a letter, digit or apostrophe into a space, then look for each entry as a whole word or a whole run of words. `no` matches "No, el jueves no" and "¡NO!", never "noviembre" or "nota"; `cancel` never matches "cancellation".

The list errs towards refusing: "sí, no hay problema" or "yes, don't worry" are refused. That is accepted, because a false refusal only makes the agent ask again, while a missed "no" saves a change the caller did not want. A refused confirmation leaves the change pending; once the caller answers again, the next `confirm_pending` reads that newer turn.

EN-04 ships unit tests for the list: every entry triggers on its own and inside a sentence in its language; case, accents and punctuation do not matter; whole-word matching holds (`noviembre`, `nota`, `cancellation`, `waiter` do not trigger); clear yeses ("sí", "vale, perfecto", "sí, por favor", "yes please", "that's fine") pass; and the known false refusals above are asserted as refused, so a change to that behaviour is deliberate.

**After a "no": one closed question, then discard.** (Decision D19.)

- The `caller_said_no` result tells the model to ask one closed yes-or-no question about the staged change (for example "¿Quiere que guarde la cita del martes a las diez, sí o no?"), not an open one. The brain prompt (VO-01) says the same.
- The session counts `caller_said_no` refusals on the current pending change. A refusal counts only if it read a newer caller turn than the previous one, so two calls on the same turn are one refusal. A save, a discard or a newly staged change resets the count.
- The **second** refusal in a row on the same pending change discards it: the result is `{"ok": false, "error": "caller_said_no", "discarded": true, "message": ...}`, the session logs `discarded` with `by: "said_no"`, and the message tells the model to say the change was not saved. Anything the caller wants next starts from a new staging call.
- EN-04 test: stage a booking; caller "no" → `confirm_pending` refused, still pending; `confirm_pending` again on the same turn → refused, still pending; caller "no, mejor no" → refused with `discarded: true`, nothing in `appointments` or `changes`, one `discarded` event with `by: "said_no"`, and a later `confirm_pending` returns `nothing_pending`. A second case: "no", then "sí" → saved.

### 5.10 Name matching (`find_patient`)

Names come from speech-to-text, so `find_patient` matches on a normalised, folded form of the name, in `app/names.py` (EN-04). Both the spoken name and the stored names go through the same steps:

1. Lowercase, strip accents (`á` → `a`, `ñ` → `n`, `ü` → `u`), turn every character that is not a letter into a space, and split into words.
2. Drop the particles `de`, `del`, `la`, `las`, `los`, `y`, `i`.
3. Fold common Spanish spelling variants, in this order: `ge`/`gi` → `je`/`ji`; `ce`/`ci` → `se`/`si`; `z` → `s`; `qu` → `k`; `c` before `a`, `o`, `u` or a consonant other than `h` → `k`; `v` → `b`; `ll` → `y`; `h` dropped unless after `c`; `y` not followed by a vowel → `i`; `x` at the start of a word → `j`; then any doubled letter becomes one.

A patient's name matches when the first word of their first name and every word of their first surname appear among the spoken words, in any order. Extra spoken words (a second surname, a middle name) are ignored. There is no edit-distance matching in the MVP: the date of birth is the second factor, and a looser match would only produce more ambiguous results.

EN-04 ships tests: these pairs match (Jiménez / Giménez, Vázquez / Basques, Hernández / Ernandes, Llorente / Yorente, Isabel / Ysabel, González / Gonsalez, Cecilia / Sesilia, Ximena / Jimena, Castillo / Castiyo, Carrasco / Carasco, "de la Fuente" / "Fuente"); these do not (Martín / Martina, Rosa / Rosas, Pérez / Peña, Luis / Luisa); case, accents and punctuation never matter; and a result never carries data of a patient other than the identified one.

### 5.11 Transcript timing at confirmation

A final caller transcript can reach the server after the model has already acted on what the caller said, most of all in the speech-to-speech layers. So `confirm_pending` does not refuse with `no_caller_reply` at once (Decision D15):

- If no non-echo caller transcript was logged after staging (an echo transcript, §7.7, does not count), `confirm_pending` waits up to `CONFIRM_TRANSCRIPT_WAIT_SECS` (default 1.0 s) for a newer final, non-echo caller transcript. If one arrives, the check (and the negative check, §5.9) runs on it; otherwise the refusal is `no_caller_reply`. The wait runs inside a `busy` scope, so it holds the silence guard.
- **Transcript missing.** If the transport detected at least `TRANSCRIPT_MISSING_MIN_SPEECH_SECS` (default 1.0 s) of caller speech and no final caller transcript for it arrives within `TRANSCRIPT_MISSING_SECS` (default 2.0 s) after the speech ended, the plumbing logs `transcript_missing` and arms the silence guard (§7.1) with its deadline `RESPONSE_TIMEOUT_SECS` after the speech ended. That is all it does: it never counts as a caller reply for `confirm_pending`, never updates `last_caller_text`, and is not read by the red-flag matcher or the dead-air prompts.
- **VO-02 measures it.** On each speech-to-speech layer, VO-02 logs, for every `ask_clinic` call, when the matching caller transcript arrived relative to the call, and reports how often it came after the call and by how much (p50, p90, max). The default of `CONFIRM_TRANSCRIPT_WAIT_SECS` is set from that measurement.

### 5.12 Read-back check

When `confirm_pending` runs on a `book`, `register_book`, `move` or `cancel`, the session compares the change's date and time (for a cancel, the cancelled appointment's) with every agent transcript logged since the change was staged (`app/readback.py`, EN-04). It recognises dates and times written as digits or spoken in either language ("10:30", "diez y media", "half past ten", "martes 10", "Tuesday the tenth").

- If the agent mentioned a date or time that differs from the change, or mentioned neither, the session logs `readback_mismatch` with `kind` `wrong`\|`missing`, the change's ISO and spoken time, and the agent text it read. The outcome carries the `readback_mismatch` flag, and the console shows it on the call (CO-01, CO-02).
- In the MVP it never refuses or delays the save. (Decision D17.)
- EN-04 tests the parser on both languages; the audio eval run (§12) shows how often real speech-to-speech read-backs trigger it.

---

## 6. Fixed phrases

### 6.1 The catalogue

Prerecorded clips, one per phrase and language, in one set per voice: `backend/app/voice/clips/<voice_set>/<id>.<lang>.wav` (mono, 16-bit, 24 kHz). They are committed, so they play even when every provider is down. The text in this table is the source; change it here and regenerate. (Decision D18.)

- **Voice sets.** Each layer names the set that sounds like its own voice, from its config: `kokoro-<KOKORO_VOICE>` for `cascade` (today `kokoro-ef_dora`), `gemini-<GEMINI_VOICE>` for `gemini` (today `gemini-kore`), `openai-<voice>` for `openai` once VO-05 picks a voice, lowercased. `none` needs no set. The active set is logged in `voice_config` (`voice_set`).
- **Pluggable generator.** `make clips VOICE_SET=<set>` (default: the active `VOICE`'s set) renders every phrase in both languages through a generator module `app/voice/clipgen/<engine>.py` that exposes `render(text, lang, voice) -> bytes` (WAV in the format above). `kokoro` runs locally at no cost. A generator that calls a paid provider (`gemini`, `openai`) refuses to run without `--paid`, and is run only with the human's approval, recorded on the board.
- **Manifest per set.** `clips/<voice_set>/manifest.json` records `voice_set`, the generator, its model and voice, and each clip's rendered text and SHA-256.
- **Start-up check.** The call server checks the active `VOICE`'s set: every phrase in both languages present, the manifest's `voice_set` equal to the active one, and every rendered text equal to the table rendered from the seed (below). It refuses to start otherwise. `CLIPS_VOICE_SET` may point a development server at another set (for example the Kokoro set while no Gemini set exists); the server logs the override (`voice_config.clips_override`). The public server never runs with it: OP-02 launches only with `CLIPS_VOICE_SET` unset.
- **No caller data.** Clip text holds only the fixed text and the clinic facts below, never anything from a call.

| ID | When | Spanish | English |
|---|---|---|---|
| `greeting` | Transport connects (§3.2) | Hola, ha llamado a {clinic_name}. Soy un asistente de inteligencia artificial y esta llamada se graba. ¿En qué puedo ayudarle? | Hello, you've reached {clinic_name}. I'm an AI assistant, and this call is recorded. How can I help you? |
| `emergency` | Red flag detected (§6.2) | En caso de dificultad para respirar, hinchazón con fiebre, un golpe fuerte en la cara o la boca, o una hemorragia que no para, llame ahora al uno uno dos o vaya a urgencias. | If you have trouble breathing, swelling with fever, a hard blow to the face or mouth, or bleeding that won't stop, call one one two now or go to an emergency department. |
| `recover` | Silence guard, first step (§7) | Perdone, un segundo. | Sorry, one moment. |
| `hold` | Brain still working after `HOLD_PHRASE_SECS` | Un momento, lo estoy mirando. | One moment, I'm checking. |
| `still_there` | Dead air after the agent spoke (§7.4) | ¿Sigue ahí? | Are you still there? |
| `technical_close` | Silence guard failed, provider error (quota included), brain down for good | Lo siento, tenemos un problema técnico. Puede llamar a la clínica al {clinic_phone}. En caso de urgencia, llame al uno uno dos. Hasta luego. | I'm sorry, we're having a technical problem. You can call the clinic on {clinic_phone}. If it's urgent, call one one two. Goodbye. |
| `cap_close` | Call cap reached | Tenemos que terminar la llamada. Si necesita algo más, vuelva a llamar. Gracias, hasta luego. | We need to end the call now. If you need anything else, please call again. Thank you, goodbye. |
| `idle_close` | Dead air after the last `still_there` | Como no le oigo, voy a colgar. Vuelva a llamar cuando quiera. Hasta luego. | I can't hear you, so I'll hang up now. Please call again any time. Goodbye. |

The language is `session.language` (`es` or `en`): at the start the page's language (`es` if none), which is the one `greeting` plays in; then set from the caller's transcripts by the voice layer: the speech-to-text language tag in the cascade, the `language` of the last `ask_clinic` in the speech-to-speech layers. Every change is logged as `language_changed`.

No phrase may contain a word that could be heard as agreement ("sí", "vale", "de acuerdo", "yes", "okay"), so a phrase can never be taken for a yes to a staged change. Spanish "si" (if) counts, because spoken it is "sí": hence "En caso de..." in `emergency` and `technical_close`.

**Placeholders.** Two phrases carry clinic facts, filled in from the `site` row (§10) when the clips are generated, never at call time:

- `{clinic_name}`: `site.name`, as written.
- `{clinic_phone}`: `site.phone` (E.164), spoken by `spoken_phone(phone, lang)` in `app/clinic.py`: the country code dropped for a Spanish number, then the nine digits read one by one in three groups of three, in the clip's language ("nueve uno cero, ..."; "nine one zero, ..."). A number outside Spain is read with its country code first ("más cuatro cuatro, ..."; "plus four four, ..."), then its national digits one by one in groups of three or four. The same function gives the brain the spoken number for `clinic_info(contact)`, so the clip and the agent say it the same way.

`make clips` reads the `site` row from the seeded database, renders every phrase, and writes the set's `manifest.json`. A test (VO-03) renders the table from the seed and fails if any rendered text differs from any committed set's manifest, so a changed name or number cannot ship with stale clips. The call server runs the same comparison for the active set at start-up (above). The seeded number (EN-01) must be one that cannot reach a real person or business. Spain has no range reserved for fiction, so the seed uses the UK Ofcom drama range, +44 20 7946 0000 to 0999, for the clinic and every patient (D25).

**Emergency phrase is conditional.** `emergency` names the red flags and says what to do *if* the caller has one, so it stays sensible when the matcher fires on a false alarm (§6.2), and it is still the right instruction when triage confirmed a red flag.

### 6.2 Emergencies: guaranteed by code

The speech-to-speech model handled a toothache in FD-01 without ever mentioning 112. So the emergency instruction is not left to any model. Three triggers, any one of which plays `emergency`:

1. **The brain's triage:** `record_triage` with `breathing_difficulty`, `trauma` or `heavy_bleeding` true, or `swelling` and `fever` both true.
2. **The brain's escalation:** `escalate` with `urgency: emergency`.
3. **A transcript matcher in code**, run on every final caller transcript, independent of both models. It matches fixed phrase lists per language (`app/redflags.py`, VO-01) for trouble breathing, swelling with fever, a blow or accident to the face or mouth, and bleeding that will not stop. It errs towards firing: a false alarm costs one conditional sentence that does not claim the caller is in danger. (Decision D2.) It also reads echo transcripts (§7.7), for the same reason.

**Tuning and holdout.** (Decision D20.) The phrase lists are tuned on `tests/redflags/tuning.yaml` (red-flag and ordinary pain phrases, both languages), and every tuning phrase must give the expected result. A separate `tests/redflags/holdout.yaml`, written before the lists and never used to change them, measures them: VO-01 reports recall on its red-flag phrases and the false-alarm rate on its other phrases, and the README quotes both. A holdout phrase that reveals a gap may be moved to the tuning set, but then a new holdout phrase is written in its place, without looking at the lists.

**Clinical sign-off.** The red-flag list (the triage fields, the matcher's phrase lists and the `emergency` text) is a demo default written without clinical review. Before any real clinic uses the agent, a licensed dentist must validate it, and the validation is recorded in the repository with the reviewer's role and date. (Decision D13.)

What happens, whichever trigger fires first:

- `CallControl.play("emergency")`, then an `escalation` event (`reason` from §5.8, `urgency: emergency`, `trigger: triage | escalate | matcher`) and an `emergency` flag on the outcome.
- **Reason for triage and matcher triggers:** trouble breathing → `red_flag_breathing`, swelling with fever → `red_flag_swelling_fever`, blow or accident → `red_flag_trauma`, bleeding that will not stop → `red_flag_bleeding`. When several flags fire at once, one `escalation` is logged with the first reason in that order, and the event's `red_flags[]` lists them all. The `escalate` trigger uses the reason the brain passed.
- **At most twice per call.** (Decision D21.) If every playback so far came from the matcher, a later `triage` or `escalate` trigger plays `emergency` once more, because the brain has now confirmed what the matcher only suspected. Otherwise later triggers do not replay it. No call hears it more than twice. Every later trigger logs `escalation` again with `repeat: true`. If the caller asks again what to do, the brain repeats the advice in its own words.
- The call goes on afterwards. The agent may take a message or book an urgency slot if the caller asks for one, but must not suggest that a dental appointment replaces emergency care. (Decision D3.)

**Pain without a red flag.** Any `urgency` search needs `record_triage` first (`triage_required`). That makes the brain ask the triage questions before offering an urgency slot, whichever voice is active.

The brain and voice prompts still tell the models about red flags. The code path is the guarantee; the prompts make the conversation around it sound right.

---

## 7. Silence guard and dead air

All of it lives in the shared plumbing, as pure logic fed with events and the timing clock (unit-testable without audio). It reads caller transcripts, not loudness, so line noise is never caller speech. It ignores every instruction the caller gives the model: telling the agent "be quiet" or "shut up" cannot disable it.

### 7.1 Response guard

FD-01 measured three failures with Gemini Live: silences of 8 to 12 s after the caller finished, caller turns skipped entirely, and an agent that stayed silent for the rest of the call after being told to shut up. The response guard covers all three.

**Final caller transcript** means a caller text that the voice layer logs as a `transcript` event with `role: "caller"`. Interim speech-to-text results are never logged as transcripts and never arm, disarm or reset anything here; the red-flag matcher and the dead-air prompts read the same events. A caller transcript logged with `echo: true` (§7.7) is not a final caller transcript for the guard or the dead-air prompts: it arms, disarms and resets nothing. Only the red-flag matcher reads it.

**It arms** when a final caller transcript arrives and the agent is not speaking at that moment. The deadline is `RESPONSE_TIMEOUT_SECS` (default 5.0 s) after the transcript arrives. It also arms on `transcript_missing` (§5.11), with the deadline counted from the end of the caller's speech.

**It disarms** when any agent audio leaves the server (model speech or a fixed phrase), or when new caller speech starts, except speech that may still be echo (§7.7). New caller speech re-arms it from the next final caller transcript. A caller turn with no agent audio before the next caller turn counts as **skipped** and is logged as such; the guard keeps running on the latest turn.

**It is held** while `CallControl.busy()` is open (brain or tool working), and never fires while a busy scope is open. A brain request ends by itself at `BRAIN_TIMEOUT_SECS` (12 s) with `brain_unavailable`. `BUSY_MAX_SECS` (15 s) is a backstop for anything else: the plumbing cancels the work, the tool returns `store_unavailable` or the brain `brain_unavailable`, logs `busy_timeout`, and closes the scope. When the hold ends, the guard re-arms with a fresh deadline, so the answer the brain just produced has time to start before a recovery could talk over it.

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
- For a layer with `supports_nudge = False`: each firing plays `recover` and counts as a failed recovery at once, so the second firing in a row ends the call.

### 7.4 Dead air after the agent spoke

Separate from the response guard. When the agent's audio has ended and no non-echo caller transcript (§7.7) arrives for `IDLE_PROMPT_SECS` (default 10 s), and nothing is busy: play `still_there`. Up to `IDLE_PROMPT_MAX` (default 2) in a row; any caller transcript that is not echo (§7.7) resets the count. After the last one, another `IDLE_PROMPT_SECS` of silence → `end("idle_close")`.

### 7.5 Other endings

- **Call cap:** at `MAX_CALL_SECS` (default 300 s) → `end("cap_close")`, log `call_cap_reached`. Built today without the closing clip.
- **Goodbye:** when the agent's transcript closes the call (a goodbye after a saved change, a recorded no-action reason, or a taken message), the plumbing ends the call after `HANGUP_GRACE_SECS` (default 3 s). Any caller transcript in the grace window that is neither a goodbye nor echo (§7.7) cancels the hang-up. A busy tool holds it until the tool returns. (VO-03.)
- **Provider errors:** every layer passes provider failures to one function, `classify_provider_error(exc) -> category`, in `app/voice/common.py`:

  | Category | What maps to it | Then |
  |---|---|---|
  | `quota` | HTTP 402, HTTP 429 or gRPC `RESOURCE_EXHAUSTED` whose message mentions quota, credit, billing or balance | `end("technical_close")`, outcome flag `provider_quota` |
  | `auth` | HTTP 401 or 403, gRPC `UNAUTHENTICATED` or `PERMISSION_DENIED` | `end("technical_close")` |
  | `unavailable` | HTTP 5xx, other HTTP 429, timeouts, refused or dropped connections | `end("technical_close")` (phase 2: reconnect) |
  | `other` | anything else | `end("technical_close")` |

  Each is logged as `provider_error`. A free-tier limit is classified like any other 429. The mapping is unit-tested per provider with recorded error payloads.

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
| `BOOKING_HORIZON_DAYS` | 28 | Furthest day `search_availability` offers |
| `URGENCY_MIN_LEAD_MINUTES` | 120 | Shortest time from `session.now` to a same-day `urgency` slot (§5.3) |
| `REVIEW_LINK_DAYS` | 7 | Review link lifetime (§11) |
| `ECHO_WINDOW_SECS` | 8 | How far back agent text is compared for echo (§7.7) |
| `ECHO_TAIL_SECS` | 0.5 | Caller speech that starts this soon after agent audio ends still counts as overlapping (§7.7) |
| `ECHO_MATCH_RATIO` | 0.8 | Share of the caller transcript's words found, in order, in recent agent text (§7.7) |
| `CONFIRM_TRANSCRIPT_WAIT_SECS` | 1.0 | `confirm_pending` waits this long for a newer caller transcript before `no_caller_reply` (§5.11) |
| `TRANSCRIPT_MISSING_MIN_SPEECH_SECS` | 1.0 | Caller speech long enough to expect a transcript (§5.11) |
| `TRANSCRIPT_MISSING_SECS` | 2.0 | Speech ended with no transcript → `transcript_missing` (§5.11) |
| `FIND_PATIENT_MAX_DOB_FAILURES` | 3 | Failed date-of-birth attempts per call before `too_many_dob_attempts` (§5.3) |
| `CLIPS_VOICE_SET` | unset | Development override of the clip set (§6.1) |

The 5 s default sits above the cascade's p90 heard latency in FD-01 (3.3 s, before overlap reporting) and well below the 8-12 s silences it must catch.

### 7.7 Echo

With speakers instead of headphones, the agent's own voice can come back through the caller's microphone and be transcribed as caller speech. An echoed read-back would then count as a caller reply, and an echoed "no" in the agent's question as a refusal. (Decision D14.)

A final caller transcript is **echo** when both hold:

1. **It overlaps agent audio:** its caller speech segment (from the transport's speech detection) starts while agent audio is going out, or less than `ECHO_TAIL_SECS` after it ended.
2. **It closely matches recent agent text:** after the normalisation of §5.9, at least `ECHO_MATCH_RATIO` of its words appear, in order, in the agent transcripts of the last `ECHO_WINDOW_SECS`. Transcripts of fewer than 3 words are never echo, so a short real "no" or "sí" is never dropped.

The plumbing checks this before logging (`app/voice/echo.py`, pure logic, VO-02) and logs the transcript with `echo: true`. An echo transcript:

- does not count as a caller reply for `no_caller_reply`, is not read by the negative check (§5.9), and does not update `last_caller_text`;
- arms, disarms and resets nothing in the silence guard, the dead-air prompts or the goodbye grace window (§7.1, §7.4, §7.5). Caller speech that could still turn out to be echo (it started during agent audio or less than `ECHO_TAIL_SECS` after it) disarms and resets nothing until its transcript is classified; if the transcript is not echo, it then acts as new caller speech from its start time, and if no transcript comes, it is handled as in §5.11;
- **is** read by the red-flag matcher (§6.2): a false alarm is cheap, a missed red flag is not;
- is shown in the console marked as echo, and counted in `audio_metrics` (`echo_transcripts`).

VO-02 unit-tests the rule with recorded timing: an echoed read-back overlapping agent audio is echo; the same words after a pause longer than `ECHO_TAIL_SECS` are not; a two-word "no, espera" over agent audio is not.

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
| `call_started` | `schema`, `now` (call clock, ISO), `monotonic`, `voice`, `channel` (`browser`\|`bench`\|`eval`\|`phone`), `language` (starting language) | Built (no `schema` or `language` yet) |
| `voice_config` | `voice` plus the layer's models and voice names, `voice_set` (§6.1), `clips_override?` | Built (`voice_set` to build) |
| `transcript` | `role` `caller`\|`agent`, `text`, `interrupted?` (agent), `language?`, `speech?` (`[start, end]` of the caller speech segment), `echo?` (caller, §7.7) | Built (`speech`, `echo` to build) |
| `transcript_missing` | `speech` (`[start, end]`), `waited_secs` (§5.11) | To build |
| `language_changed` | `language` | To build |
| `greeting_latency` | `secs` | Built |
| `latency` | `secs` (server: caller stopped → first agent audio out) | Built |
| `latency_breakdown` | `measured_from`, `total_secs`, `stages[]` | Built |
| `heard_latency` | `secs` (measured by the browser page or the bench), `overlapped?` (§13: agent audio started before the caller finished or under 150 ms after) | Built (`overlapped` VO-06) |
| `heard_unanswered` | – (a caller turn the page or the bench heard no answer to; §13) | Built (VO-06) |
| `usage` | `service` `llm`\|`s2s`\|`stt`\|`tts`\|`brain`, `model`, token or character counts | Built (`brain`, `stt` to build) |
| `brain_request` / `brain_reply` | `request`, `language` / `say`, `phrase`, `secs`, `error?` | To build |
| `tool_call` / `tool_result` | `id`, `name`, `args` / `id`, `ok`, `result` or `error`, `message` | To build |
| `staged` | `change` (`register`\|`book`\|`register_book`\|`move`\|`cancel`), `details`, `operation_key` | To build |
| `saved` | `change`, `operation_key`, `change_ids[]` (one id; for `register_book` the register id then the book id), `already_saved` | To build |
| `discarded` | `change`, `operation_key`, `by` (`tool`\|`finish`\|`race`\|`said_no`) | To build |
| `readback_mismatch` | `change`, `operation_key`, `kind` `wrong`\|`missing`, `expected` (ISO and spoken), `heard` (§5.12) | To build |
| `triage` | the six answers, `red_flags[]` | To build |
| `escalation` | `reason`, `urgency`, `trigger`, `red_flags?` (triage and matcher triggers), `repeat?` | To build |
| `message_taken` | `message_id`, `urgent` | To build |
| `phrase_played` | `phrase`, `language`, `voice_set`, `secs`, `interrupted?` (§3.4) | To build |
| `silence_guard_fired` | `turn`, `waited_secs`, `attempt` | To build |
| `silence_guard_recovered` | `turn`, `extra_secs` | To build |
| `turn_skipped` | `turn` | To build |
| `voice_unresponsive` | `guard_firings` | To build |
| `busy_timeout` | `what`, `secs` | To build |
| `dead_air_prompt` | `count` | To build |
| `provider_error` | `provider`, `category` `quota`\|`auth`\|`unavailable`\|`other`, `detail` | To build |
| `voice_error` / `voice_unavailable` | `error` / `voice` | Built |
| `caller_hung_up` / `call_cap_reached` / `agent_hung_up` | – / `secs` / `phrase?` | Built / built / to build |
| `recording_saved` | `path`, `secs`, `rate` | To build (VO-04) |
| `audio_metrics` | §9.3 | To build (VO-04) |
| `outcome` | `result` `changed`\|`no_action`, `changes[]` (change IDs), `no_action_reason?`, `derived`, `escalations[]`, `flags[]` | To build |
| `review_link_created` | `expires_at` (never the token) | To build (CO-05) |
| `call_ended` | `duration_secs` | Built |

`outcome` is written exactly once, by `finish()`, before `call_ended`. Its `flags` come from this list: `emergency`, `provider_quota`, `voice_unresponsive`, `call_cap`, `brain_error`, `same_day_urgent` (a same-day urgency booking was saved), `urgent_callback` (an urgent callback message was taken), `readback_mismatch` (§5.12). The console shows `readback_mismatch` on the call list and the review page. The console's "quota" filter reads `provider_quota`; its "needs staff today" filter reads `same_day_urgent` and `urgent_callback`.

---

## 9. Recording

### 9.1 Calls (to build, VO-04)

- **Where:** `recordings/<call_id>.wav` at the repository root, git-ignored, next to `recordings/<call_id>.timing.json`.
- **Format:** WAV, 2 channels, 16-bit PCM, 24 kHz. **Left: the caller**, as the server received it. **Right: the agent**, as the server sent it, fixed phrases included.
- **One shared timeline:** sample `n` is at `t = n / 24000` on the timing clock, the same `t` as the call log. Sample 0 is `t = 0` (`call_started`). Where a side sent nothing, its channel is silence. So `t` from any log event is a position in the recording, which is how the console jumps to a message.
- The recorder taps the transport, below the voice layer, so it is the same for every `VOICE`. It resamples both channels to 24 kHz before writing (Gemini, for example, takes 16 kHz in and sends 24 kHz out) and places each frame by the timing-clock time it was received or sent.
- `timing.json`: `{"rate": 24000, "caller": [[start, end], ...], "agent": [[start, end], ...], "underruns": [t, ...]}`, speech segments in seconds on the same timeline.

### 9.2 Bench recordings (built)

`scripts/voice_bench.py` saves `recordings/bench/<voice>/<call_id>.wav` in the same channel layout and rate, but on the **caller's** clock (each frame placed when it left or reached the scripted caller), because it measures what the caller hears. Do not mix the two timelines when comparing.

### 9.3 Audio metrics (`audio_metrics` event, after the call)

`greeting_secs`, `turns`, `answered_turns`, `overlapped_turns`, `overlap_rate`, `latency_p50_ms`, `latency_p90_ms`, `skipped_turns`, `guard_firings`, `dead_air_secs` (total gaps over 3 s with nobody speaking), `barge_ins`, `yield_p50_ms` (caller starts over agent → agent audio stops), `underruns`, `echo_transcripts` (§7.7), `transcripts_missing` (§5.11).

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
                   same_day_urgent INTEGER NOT NULL DEFAULT 0 CHECK (same_day_urgent IN (0,1)),
                   created_by_call TEXT);                        -- same_day_urgent: §5.3, set by the store
CREATE TABLE changes (change_id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
                   call_id TEXT NOT NULL, action TEXT NOT NULL
                   CHECK (action IN ('register','book','move','cancel','demo_reset')),
                   details TEXT NOT NULL, saved_utc TEXT NOT NULL);  -- details: JSON before/after
CREATE TABLE messages (message_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, name TEXT NOT NULL,
                   phone TEXT NOT NULL, text TEXT NOT NULL,
                   urgent INTEGER NOT NULL DEFAULT 0 CHECK (urgent IN (0,1)), saved_utc TEXT NOT NULL);
CREATE TABLE review_links (token_sha256 TEXT PRIMARY KEY, call_id TEXT NOT NULL UNIQUE,
                   expires_utc TEXT NOT NULL);
```

**No overlapping appointments, enforced by the database.** SQLite has no exclusion constraint, so two triggers, `BEFORE INSERT` and `BEFORE UPDATE OF start_utc, end_utc, practitioner_id, status ON appointments`, `RAISE(ABORT, 'overlap')` when the new or changed row is `booked` and another `booked` row for the same practitioner, or the same patient, has `start_utc < NEW.end_utc AND end_utc > NEW.start_utc`. `BEGIN IMMEDIATE` serialises writers, so the trigger's check and the write cannot interleave. The store turns `overlap` into `slot_taken` or `patient_overlap`. EN-03 tests two concurrent bookings of one slot: exactly one succeeds.

**Operation keys.** Each staged change gets `operation_key = "<call_id>:<n>"`, where `n` counts the changes staged in the call (1, 2, ...). The key and the write go in one transaction. A second save with a key already in `changes` writes nothing and returns the first result. A move is one `UPDATE` of the appointment; a cancel sets `status = 'cancelled'`. Neither deletes rows.

**Registration plus booking.** When `book` extends a staged registration (§5.3), the pending change becomes `register_book`: one pending change and one `operation_key`. The log is append-only, so the session appends a second `staged` event with `change: register_book` and the same `operation_key` (its `details` hold both the patient and the booking); the earlier `staged` `register` line stays, and readers take the latest `staged` event per key. Saving writes two `changes` rows in one transaction: `action = 'register'` with key `<call_id>:<n>/register`, and `action = 'book'` with key `<call_id>:<n>`. The idempotency check looks up `<call_id>:<n>`; a registration-only change uses `<call_id>:<n>` for its single `register` row. `saved` and `discarded` carry `change: register_book` and the key `<call_id>:<n>`; `saved.change_ids` and `outcome.changes[]` list both rows' ids, register first, and a retry with `already_saved: true` returns the same pair.

---

## 11. Review link and public calls (CO-05)

- **Before the call.** The page with the talk button (landing and Studio, CO-03) states, next to the button and before the microphone is requested, that the call is recorded and that the caller will talk to an AI, and how long the recording is kept (`REVIEW_LINK_DAYS`). It also warns visitors not to give real names, health details or phone numbers, and to use the made-up details of a Studio card instead. The microphone is requested only after the visitor has seen both. The opening of the call says the recording and AI part (`greeting`, §6.1). (Decisions D10, D23.)
- **Which calls get a link.** Only calls on a public channel (`browser` today, `phone` in phase 2) get a review link, and every such call gets one. `bench` and `eval` calls never do, so the purge below never touches them.
- When such a call ends, the session creates a token with `secrets.token_urlsafe(32)` (256 random bits). The link is `/r/<token>` on the public call server.
- Only `sha256(token)` is stored (`review_links`), with an expiry of `REVIEW_LINK_DAYS` (default 7) days. The token itself is shown once, to the caller's browser, at hang-up. It never appears in the log.
- The public route serves that one call's review: transcript, recording and tool steps, **redacted**: tool arguments and results show only the identified patient's own data (`find_patient` never returns anyone else's, §5.3), and a `find_patient` that identified no one shows only its count. (Decision D10.)
- An unknown, expired or malformed token returns 404, the same response in each case. No public route lists calls or accepts a `call_id`.
- **At expiry the data goes, not just the link.** For each expired `review_links` row, a purge (`scripts/purge_expired.py`, run hourly by a timer and once at call-server start-up, OP-02) deletes everything kept for that call: the call log `data/calls/<call_id>.jsonl`, `recordings/<call_id>.wav` and `.timing.json`, the call's `messages` rows, patients registered in that call (`created_by_call`) with their appointments, appointments created by that call (`created_by_call`), the `changes` rows of that call, and finally the `review_links` row. Moves and cancellations of seeded appointments hold no caller data and are undone by the nightly demo reset (OP-01). Each purge appends one line to `data/purge.log` with the `call_id`, the time and the counts deleted, and nothing else. The console loses the call at the same moment. Calls with no review link (`bench`, `eval`) are not touched. CO-05 tests that after the purge no file or row with that `call_id` remains.

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
  flags: []             # outcome flags that must be set (§8), e.g. [same_day_urgent]; others must not
  forbidden_tools: []   # e.g. [book] for an info-only call
judge:                  # rubric items on top of the default ones
  - "Repeats the time slowly when asked"
```

Default checks on every scenario: database state equals `expect.changes` and nothing else; every save came after `confirm_pending`; no `slot_not_offered` was ever bypassed; a discarded change was never saved; a no-action call has a reason from §5.7; the agent stayed in the caller's language (following any switch); no medical advice; polite.

Text-only evals run the brain and tools with no voice layer: the simulated caller's lines are the caller transcripts, and the brain's `say` is the agent transcript. Fixed phrases are logged as `phrase_played` and appear in the agent transcript as their text.

**What the text evals measure.** They measure the brain's decisions: which tools it calls, what it stages and saves, and what it asks the voice to say. They do not measure what a speech-to-speech layer actually says, which can differ from the brain's `say` (a wrong time read back, a fact no tool returned). The audio eval run (EV-04) covers that: for each scenario it checks the read-back against the staged change (the `readback_mismatch` events, §5.12) and flags invented facts, meaning any date, time, price or name in the agent transcript that no tool result or fixed phrase gave. (Decision D17.)

---

## 13. Metrics: how numbers are reported

- **Every latency figure goes out with its overlap rate and its count.** The latency distribution leaves out overlapped turns (the agent's audio started before the caller finished, or less than 150 ms after), so a median alone can look better than the call felt: in one Gemini browser test 4 of 8 turns overlapped. Format, with placeholder values: `p50 <P50> / p90 <P90> ms (n = <N> answered, overlap <X>%, <U> unanswered)`. (Decision D24.)
- **Overlap rate** = overlapped turns / (answered + overlapped turns).
- **Unanswered turns** (the guard fired, or the turn was skipped) are reported as a count beside the latency, never folded into it.
- **Say which latency.** *Heard* latency is measured at the caller (bench or browser page); *server* latency is caller stopped → first agent audio out at the server. They differ by the network and the caller's audio path. Tables name which one they show.
- **Cost** is reported per call minute and per call, with the date of the prices assumed (`PRICES_AS_OF` in `scripts/call_costs.py`), and whether the run was billed (free-tier runs are priced at paid rates).
- **Text evals are not voice evals.** Task success from the text evals (§12) is a measure of the brain's decisions, not of what a speech-to-speech layer says. Tables say which run a number comes from; read-back errors and invented facts are reported only from the audio run (EV-04).
- Bench and browser reports (VO-06) and the README tables follow these rules.

---

## 14. Decisions

D1 to D13 were decided by the human on 2026-11-08. D14 to D24 are changes the human asked for on 2026-11-08, after the review of D1 to D13. This file already specifies each final decision; changing one now follows the rule at the top of this file.

| # | Topic | Status | Final decision | Where |
|---|---|---|---|---|
| D1 | Same-day slots | **Decided: changed** | Same-day slots only for `urgency`, at least `URGENCY_MIN_LEAD_MINUTES` (default 120) after the call clock's `now`; every other treatment still refuses same-day with `same_day`; an urgency slot today that starts less than the lead time ahead is refused with `too_soon`. Saved same-day urgency bookings are flagged for staff in the console. With no slot today, the agent takes an urgent callback with `take_message` | §5.3, §5.6, §7.6, §8, §10 |
| D2 | What the red-flag transcript matcher does | **Decided: accepted with a change** | The matcher plays `emergency` itself, accepting false alarms; the phrase is conditional ("if you have trouble breathing... call 112"), in Spanish and English, so a false alarm does not sound absurd | §6.1, §6.2 |
| D3 | Booking after an emergency referral | **Decided: accepted** | Allowed only if the caller asks; the agent must not imply it replaces emergency care | §6.2 |
| D4 | Silence guard timeout | **Decided: accepted** | 5.0 s (`RESPONSE_TIMEOUT_SECS`) | §7.1, §7.6 |
| D5 | When silence recovery fails twice | **Decided: accepted with a change** | End the call with `technical_close` and flag it. The closing phrase gives the clinic's phone number and says to call 112 if it is urgent; the number is filled in from `site.phone` when the clips are generated, checked against a manifest | §6.1, §7.3 |
| D6 | Cascade architecture | **Decided: accepted with an addition** | The cascade's text model is the brain, with the tools directly. It uses the same brain prompt and the same tools as the brain behind `ask_clinic`, so the text evals apply to both voice options | §4.3 |
| D7 | What identifies a patient | **Decided: changed** | Always name plus date of birth. The caller-ID path is removed (no `phone` in `find_patient`, no caller ID in the log, prompt or scenarios). Caller ID is a phase 2 topic | §5.2, §5.3, §8, §12 |
| D8 | When a new patient is saved | **Decided: accepted** | Staged, and saved by `confirm_pending` in one transaction with the first booking when there is one; the date of birth is required | §5.3 |
| D9 | Lists and tools added to PLAN.md | **Decided: accepted** | No-action reasons `emergency_referred`, `technical_failure`, `caller_hung_up`; tools `record_triage` and `take_message` | §5.3, §5.7 |
| D10 | Public review link and public calls | **Decided: accepted with additions** | The link shows transcript, recording and redacted tool steps for 7 days. At expiry the recording and all call data behind the link are deleted, not just the link. Before the call, the page and the opening clip say the call is recorded and the caller is talking to an AI. Launch requirement: the public demo never runs on a provider tier whose terms let the provider use call data | §3.1, §3.2, §6.1, §11 |
| D11 | Confirmation check | **Decided: changed** | `confirm_pending` still needs a caller turn after staging, and is also refused with `caller_said_no` when the latest final caller turn contains a clear negative from a bilingual whole-word list. A false refusal only makes the agent ask again. EN-04 ships unit tests for the list | §5.3, §5.9 |
| D12 | Default `VOICE` in code | **Decided: accepted** | `cascade` until the Gemini paid tier and VO-05; no automatic failover | §3.1 |
| D13 | Red-flag list | **Decided: accepted with an addition** | PLAN.md's three (trouble breathing, swelling with fever, trauma) plus bleeding that will not stop. A licensed dentist must validate the list before any real clinic uses it | §6.2 |
| D14 | Echo | **Decided: asked by the human** | A caller transcript that overlaps agent audio and closely matches recent agent text is logged with `echo: true`. It counts for neither `no_caller_reply` nor the negative check, and arms nothing in the silence guard, the dead-air prompts or the goodbye grace window. The red-flag matcher still reads it. Transcripts under 3 words are never echo | §3.2, §5.2, §5.11, §7.1, §7.4, §7.5, §7.7, §8 |
| D15 | Transcript timing | **Decided: asked by the human** | Before refusing with `no_caller_reply`, `confirm_pending` waits up to `CONFIRM_TRANSCRIPT_WAIT_SECS` (1.0 s) for a newer caller transcript. At least 1 s of detected caller speech with no transcript within `TRANSCRIPT_MISSING_SECS` logs `transcript_missing` and arms only the silence guard; it is never a reply. VO-02 measures transcript arrival against tool calls on each speech-to-speech layer | §5.11, §7.1, §7.6, §8 |
| D16 | What `find_patient` returns | **Decided: asked by the human** | Patient data only for the one match with that date of birth; otherwise a match count and a hint. At most 3 failed date-of-birth attempts per call, then `too_many_dob_attempts`. Names are matched with case, accents and particles ignored and common Spanish spelling variants folded, with tests. `too_many_matches` is removed | §5.2, §5.3, §5.4, §5.10, §11 |
| D17 | Read-back and what text evals measure | **Decided: asked by the human** | At `confirm_pending`, the staged date and time are compared with the agent transcript since staging; a mismatch is logged as `readback_mismatch` and flagged in the console, never blocking in the MVP. Text evals measure the brain's decisions; the audio run covers read-back and invented facts | §5.3, §5.12, §8, §12, §13 |
| D18 | Clips per voice | **Decided: asked by the human** | One clip set per voice at `clips/<voice_set>/<id>.<lang>.wav`, rendered by a pluggable generator, with the set recorded in its manifest and checked for the active `VOICE` at start-up. Clip text holds no caller data. Paid generators run only with the human's approval | §6.1, §7.6, §8 |
| D19 | After a "no" | **Decided: asked by the human** | After `caller_said_no` the agent asks a closed yes-or-no question. A second refusal in a row on the same pending change discards it, and the agent says it was not saved. EN-04 tests it | §5.3, §5.7, §5.9, §8 |
| D20 | Red-flag matcher tuning | **Decided: asked by the human** | Tuned on one phrase set, measured on a holdout set never used for tuning; recall and false-alarm rate are reported | §6.2 |
| D21 | Emergency replay | **Decided: asked by the human** | If every playback so far came from the matcher, a later triage or escalate trigger plays `emergency` once more; at most two playbacks per call | §5.3, §6.2 |
| D22 | Barge-in on clips | **Decided: asked by the human** | `greeting`, `emergency` and `technical_close` always play to the end, so the clinic's number and the 112 reminder are always heard in full; every other clip stops when the caller starts speaking | §3.4, §8 |
| D23 | Warning before the microphone | **Decided: asked by the human** | The page warns visitors, before the microphone is requested, not to give real names, health details or phone numbers | §11 |
| D24 | Metrics example | **Decided: asked by the human** | The reporting format in §13 uses placeholder values, not measured ones | §13 |
| D25 | Fictional phone numbers | **Decided: asked by the human** (2026-11-10, EN-01) | `site.phone` and every patient phone come from the UK Ofcom drama range +44 20 7946 0000 to 0999, behind one constant in `seed/phones.py`; `spoken_phone` reads a non-Spanish number with its country code. Additive | §6.1, §10 |
