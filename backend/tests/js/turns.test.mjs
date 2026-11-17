// Run with: node --test tests/js/   (part of `make test`)
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatSummary,
  median,
  pct,
  summarise,
  TurnMeter,
} from "../../app/static/turns.js";

// Same latencies as tests/test_latency_report.py SHARED_LATENCIES (as browser events).
const SHARED_EVENTS = [
  { kind: "turn", ms: 1500 },
  { kind: "overlap", ms: 100 },
  { kind: "unanswered" },
  { kind: "turn", ms: 2000 },
  { kind: "overlap", ms: -50 },
];

const TICK = 16; // ms, one animation frame

// A call script: [durationMs, callerLevel, agentLevel] segments, played tick by tick.
function play(segments, meter = new TurnMeter()) {
  const events = [];
  let now = 0;
  for (const [ms, you, agent] of segments) {
    for (const end = now + ms; now < end; now += TICK) {
      const e = meter.update(now, you, agent);
      if (e) events.push(e);
    }
  }
  return events;
}

const SPEECH = 0.1, ECHO = 0.03, SILENT = 0;
const greeting = [[400, SILENT, SILENT], [2500, SILENT, SPEECH], [1000, SILENT, SILENT]];

test("median of an even count is the mean of the two middle values", () => {
  assert.equal(median([40, 3040, 3580, 3653]), 3310);
  assert.equal(median([3653, 40, 3580, 3040]), 3310);
});

test("median of an odd count is the middle value; none for no values", () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([]), null);
});

test("a normal turn reports the time from the caller's last speech to the answer", () => {
  const events = play([
    ...greeting,
    [1500, SPEECH, SILENT],
    [1200, SILENT, SILENT],
    [2000, SILENT, SPEECH],
  ]);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "turn");
  assert.ok(Math.abs(events[0].ms - 1200) <= 2 * TICK, `got ${events[0].ms}`);
});

test("echo of the greeting in the microphone is not a turn", () => {
  // The greeting is two sentences with a pause between them, and the microphone
  // hears the agent (AEC not converged yet), as in the Chrome test that showed 40 ms.
  const events = play([
    [400, SILENT, SILENT],
    [1500, ECHO, SPEECH],
    [900, SILENT, SILENT],
    [1500, ECHO, SPEECH],
    [1000, SILENT, SILENT],
  ]);
  assert.deepEqual(events, []);
});

test("echo at the start of an answer does not shorten the measured latency", () => {
  const events = play([
    ...greeting,
    [1500, SPEECH, SILENT],
    [1000, SILENT, SILENT],
    [2000, ECHO, SPEECH], // the mic picks the answer up from the first frame
    [1000, SILENT, SILENT],
  ]);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "turn");
  assert.ok(events[0].ms >= 1000 - 2 * TICK, `got ${events[0].ms}`);
});

test("the caller talking over the greeting does not start a turn", () => {
  const events = play([
    [400, SILENT, SILENT],
    [2500, SPEECH, SPEECH],
    [600, SILENT, SILENT], // shorter than the agent's gap: still the greeting
    [800, SILENT, SPEECH],
    [1000, SILENT, SILENT],
  ]);
  assert.deepEqual(events, []);
});

test("an agent that starts while the caller is still talking is an overlap", () => {
  const events = play([
    ...greeting,
    [1500, SPEECH, SILENT],
    [1500, SPEECH, SPEECH],
  ]);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "overlap");
});

test("a click shorter than the minimum speech does not start a turn", () => {
  const events = play([
    ...greeting,
    [100, SPEECH, SILENT],
    [3000, SILENT, SILENT],
    [1000, SILENT, SPEECH],
  ]);
  assert.deepEqual(events, []);
});

test("summarise and formatSummary match the Python shared example", () => {
  const s = summarise(SHARED_EVENTS);
  assert.equal(s.answered.length, 2);
  assert.equal(s.overlapped, 2);
  assert.equal(s.unanswered, 1);
  assert.ok(Math.abs(s.p50 - 1.75) < 1e-9);
  assert.ok(Math.abs(s.p90 - 1.95) < 1e-9);
  assert.equal(
    formatSummary(s),
    "heard: p50 1750 / p90 1950 ms (n = 2 answered, overlap 50%, 1 unanswered)",
  );
});

test("pct uses the inclusive method like Python", () => {
  const vals = [1.49, 1.5, 1.6, 2.0];
  assert.ok(Math.abs(pct(vals, 50) - 1.55) < 1e-9);
  assert.equal(pct([2.5], 50), 2.5);
});

test("unanswered fires once after 8 s without an agent answer", () => {
  const meter = new TurnMeter({ unansweredMs: 8000 });
  const segments = [
    ...greeting,
    [1500, SPEECH, SILENT],
    [8100, SILENT, SILENT],
  ];
  const events = play(segments, meter);
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "unanswered");
});

test("unanswered does not fire before 8 s", () => {
  const meter = new TurnMeter({ unansweredMs: 8000 });
  const events = play(
    [...greeting, [1500, SPEECH, SILENT], [7900, SILENT, SILENT]],
    meter,
  );
  assert.equal(events.filter((e) => e.kind === "unanswered").length, 0);
});

test("an agent answer at 7.9 s prevents unanswered", () => {
  const meter = new TurnMeter({ unansweredMs: 8000 });
  const events = play(
    [
      ...greeting,
      [1500, SPEECH, SILENT],
      [6400, SILENT, SILENT],
      [500, SILENT, SPEECH],
    ],
    meter,
  );
  assert.deepEqual(events.map((e) => e.kind), ["turn"]);
});

test("a new caller turn can arm again after unanswered", () => {
  const meter = new TurnMeter({ unansweredMs: 8000 });
  const events = play(
    [
      ...greeting,
      [1500, SPEECH, SILENT],
      [8100, SILENT, SILENT],
      [1500, SPEECH, SILENT],
      [1200, SILENT, SILENT],
      [500, SILENT, SPEECH],
    ],
    meter,
  );
  assert.deepEqual(events.map((e) => e.kind), ["unanswered", "turn"]);
});

test("each answer is one turn, however many sentences it has", () => {
  const events = play([
    ...greeting,
    [1000, SPEECH, SILENT],
    [900, SILENT, SILENT],
    [1500, SILENT, SPEECH],
    [300, SILENT, SILENT], // pause between sentences of one answer
    [1500, SILENT, SPEECH],
    [1000, SILENT, SILENT],
    [1000, SPEECH, SILENT],
    [1100, SILENT, SILENT],
    [1500, SILENT, SPEECH],
  ]);
  assert.deepEqual(events.map((e) => e.kind), ["turn", "turn"]);
  assert.ok(Math.abs(events[0].ms - 900) <= 2 * TICK);
  assert.ok(Math.abs(events[1].ms - 1100) <= 2 * TICK);
});

test("an agent answer on the very tick unanswered would fire still counts as a turn", () => {
  const meter = new TurnMeter({ unansweredMs: 8000 });
  for (const [t, you, agent] of [[0, 0, 0.1], [500, 0, 0.1], [1500, 0, 0], [2000, 0.1, 0], [2500, 0.1, 0]]) {
    meter.update(t, you, agent);
  }
  // The caller stopped at 2500; the agent starts exactly 8 s later.
  const event = meter.update(10500, 0, 0.1);
  assert.equal(event.kind, "turn");
});
