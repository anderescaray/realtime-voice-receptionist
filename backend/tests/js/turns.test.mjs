// Run with: node --test tests/js/   (part of `make test`)
import assert from "node:assert/strict";
import { test } from "node:test";

import { median, TurnMeter } from "../../app/static/turns.js";

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
