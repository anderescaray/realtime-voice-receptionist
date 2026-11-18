# Eval Scenarios

Each scenario (`backend/evals/scenarios/<id>.yaml`) defines one simulated caller conversation against the seeded clinic database at a fixed call clock (`now`). It pairs caller persona, facts, and goal with expected database/call outcomes (`expect`) and rubric items (`judge`).

See [`contracts.md` §12](../../contracts.md) for the exact YAML schema and closed vocabularies (`evals/scenarios.py` loads and validates them; an optional top-level `skip: "<reason>"` makes pytest skip seed checks for that scenario).

## Semantics of `expect.changes` and `facts.wrong_dobs`

- `{action: book, treatment, patient, practitioner?, after, before, part_of_day?}`: one booking saved for `patient` (`patient: new` for a patient registered in the same call) whose local start date `D` satisfies `after <= D < before` (`after` inclusive, `before` exclusive), with `practitioner` if given, where `morning` starts before `14:00` and `afternoon` starts at `14:00` or later (`Europe/Madrid`).
- `{action: register, patient: new}`: the caller is registered with `facts.name`, `facts.dob`, and `facts.phone` (paired with a `book` for `patient: new`).
- `{action: move, appointment, treatment, practitioner?, after, before, part_of_day?}`: moves `appointment` to a slot in `[after, before)`.
- `{action: cancel, appointment}`: cancels `appointment`.
- `facts.wrong_dobs`: list of `YYYY-MM-DD` dates the caller gives before the right `dob` (or before exhausting lookup attempts); `facts.name` matches at least one seeded patient, and none of the wrong dates matches any of them.

## How the other `expect` fields are read

- `no_action_reason` is compared with the call's final reason, recorded by `record_no_action` (the last one wins) or derived at `finish()` (contracts.md §5.7). Scenarios whose reason depends on the agent recording it (`message-*`, `adversarial-es-02`, `move-en-02`) say so in a judge item.
- `escalation: null` means no emergency escalation may happen; a non-emergency escalation (for example `medical_advice_request`) is allowed and not required. A non-null `escalation` is compared with the first escalation that is not a `repeat`.
- `flags` must be set and no other outcome flag may be (§12).
- Callers in urgent and red-flag scenarios answer triage questions with a plain "no" and never repeat symptom words they deny ("no fever"), because the red-flag transcript matcher is built to fire easily (§6.2) and would turn a denial into a false alarm.
- `move-en-02` covers the agent's behaviour after two "no"s (decision D19: closed question, nothing saved). A correct agent may discard the move itself rather than through a second refused `confirm_pending`.

## ID Formats (`contracts.md` §5.1) and Temporary Seed Mapping

Until task EN-05 migrates the seed to §5.1 IDs, `seed_id()` in the test maps scenario IDs to current seed IDs when the new ID is not yet in the database. EN-05 has this exact mapping on its card, so the scenarios keep pointing at the same rows after the migration:

| Entity | Scenario ID (§5.1) | Current Seed ID |
|---|---|---|
| Patient | `p_\d{4}` (e.g. `p_0123`) | `p0123` |
| Appointment | `a_[0-9a-f]{8}` (e.g. `a_00000648`) | `a00648` (`"a" + digits[-5:]` when leading digits are zeros) |
| Practitioner | `d_iglesias`, `d_moya`, `d_ferrer`, `d_pena`, `d_torres` | `dent_1`, `dent_2`, `dent_3`, `dent_4`, `hyg_1` |

## Running the Tests

```bash
cd backend && uv run pytest tests/test_eval_scenarios.py -q
```
