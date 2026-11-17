# Overlaps, barge-in and echo (VO-06)

Status: draft. The bench findings below come from the existing logs; the browser
section waits for the speakers and headphones test calls.

## How latency is reported now

Every latency figure goes out in the format of contracts §13:
`p50 <P50> / p90 <P90> ms (n = <N> answered, overlap <X>%, <U> unanswered)`.

- A turn is **answered** when agent audio started at least 150 ms after the caller
  stopped. Only answered turns are in p50 and p90.
- **Overlapped**: agent audio started before the caller stopped, or less than 150 ms
  after. Overlap rate = overlapped / (answered + overlapped).
- **Unanswered**: no agent audio for that caller turn (the bench waits 12 s, the
  browser page 8 s). Reported as a count, never folded into the latency.

Where it shows up:

| Report | Command or place | Latency |
|---|---|---|
| Bench run | `scripts/voice_bench.py` (per call and for the run) | heard |
| Bench logs after the fact | `uv run python -m scripts.bench_report` | heard, one line per date and setup |
| Call logs | `make costs` (`scripts/call_costs.py`) | heard per call and per setup; server per setup, without an overlap rate, because the server cannot see overlaps |
| Browser test page | the line under the turn table | heard |

The page and the bench now also send overlapped and unanswered turns to the server
(`/api/heard` with `kind`), logged as `heard_latency` with `overlapped: true` and as
`heard_unanswered` (contracts §8).

## FD-01's figures, republished

Recomputed from the bench logs of 2026-11-07 (the final setups), heard latency at the
scripted caller:

| Setup | Calls | Heard latency |
|---|---:|---|
| A1 Gemini Live (`gemini-3.8-live`) | 30 | p50 1490 / p90 2048 ms (n = 104 answered, overlap 12%, 0 unanswered) |
| B cascade (Flux, Haiku 5.5, Kokoro) | 8 | p50 2522 / p90 3204 ms (n = 32 answered, overlap 0%, 0 unanswered) |

- FD-01 quoted 1490 / 1991 ms (n = 46) and 2490 / 3281 ms (n = 28). These use every
  logged run of that day, so the counts are larger and p90 moved a little.
- The old summary was a median over every turn, overlaps included (as negative times).
  For Gemini that gave 1459 ms: the overlaps pulled it down.
- "0 unanswered" is a floor, not a measurement: before VO-06 the bench did not write
  down turns that got no answer.
- Earlier cascade runs (2026-11-05, an older configuration) are slower and are not
  part of these figures; `bench_report` lists them on their own lines.

## What the bench overlaps are

The bench caller is clean digital audio, with no speaker and no microphone, so its
overlaps cannot be echo. In the Gemini recordings, agent audio starts inside caller
speech 13 times; the cascade has none.

In nearly every case the pattern is the same:

1. The agent starts answering, says a few words ("Como le comentaba, ...") and then
   goes quiet for 1.5 to 3 s in the middle of its answer.
2. The bench treats 1.5 s of agent silence as the end of the answer and starts the
   next caller line.
3. The agent resumes its sentence about 0.02 to 0.1 s after the caller starts,
   talks over the caller, and is then cut off (`interrupted`).

Evidence:

- The audio the bench hears over its next line is the rest of the previous answer.
  For 11 of the 14 overlapped turns, the server's `latency` event for that answer came
  1.8 to 2.7 s earlier, and the agent transcript is a single sentence spanning the
  gap. Two more are the greeting, resumed over the first caller line.
- Many of these answers are repeats ("Le repito...", "Le recuerdo..."), so the stall
  may come from the model starting a long answer, not from the network.

Open question: the resumption lands very close to the start of caller speech, more
often than chance would suggest. Two explanations fit:

- **(a) The model stalls mid-answer, and the bench's 1.5 s rule is too short.** The
  overlap is then partly a bench artefact. But a real caller would also hear a
  1.5 to 3 s silence in mid-sentence and start talking.
- **(b) Agent audio is held somewhere between the model and the bench** (output
  pacing or the receiving side's buffering) and released when caller audio starts
  flowing.

The server-side recorder (VO-04) settles it. Its timeline has the agent audio as it
left the server, so it shows whether that audio left before or after the caller
started.

## Barge-in

What the logs show so far:

- When caller speech starts over the agent, the agent stops. The agent transcript is
  logged with `interrupted: true`, and in the bench cases above the resumed answer is
  cut short.
- The `interrupted` flag is not reliable for Gemini: 10 of 32 Gemini greetings are
  marked interrupted, some before the caller had spoken. The yield time (caller starts
  → agent audio stops, `yield_p50_ms` in §9.3) needs the recorder (VO-04), not this
  flag.

## Echo in browser calls (speakers versus headphones)

To fill in after the test calls: one on laptop speakers, one on headphones, same
script (a barge-in, a mid-sentence pause, ten seconds of silence).

What to look for in each call's log and page summary:

- caller transcripts that repeat the agent's own words (echo heard as caller speech);
- agent turns cut off with no real caller speech (the agent interrupted by its own
  voice);
- overlap rate and unanswered count on speakers versus headphones.

## Proposed fix

Preliminary; to be confirmed by the browser test.

1. **Bench: stop mistaking a pause for the end of an answer.** Use the server's own
   "agent stopped speaking" signal, or a longer agent silence (about 3 s), before
   playing the next caller line. Count mid-answer agent silences over 1 s as a
   separate metric, because a real caller hears them as dead air.
2. **Measure on the server side.** With the recorder (VO-04), compute overlap,
   barge-in yield and mid-answer gaps on the server timeline, so (a) and (b) above can
   be told apart, and the bench and the browser stop being the only witnesses.
3. **Echo** (pending the test): the browser already asks for echo cancellation. The
   transcript rule of contracts §7.7 (VO-02) keeps echoed text from counting as a
   caller reply. If the test shows the agent cutting itself off on speakers, the fix
   is in the interruption rule: during agent speech, interrupt only on speech that
   lasts long enough or carries a few words, at the cost of a slower yield.
