// Turn detection for the test call page: feed it the caller's and the agent's audio
// levels on every animation frame, and it reports each answered turn with the latency
// the caller heard. Pure logic (no DOM, no clock of its own), so it can be unit-tested.
//
// What counts as a turn:
// - The greeting is not a turn. Turns are measured only once the agent's first speech
//   (the greeting) has been followed by a pause.
// - Caller speech counts only while the agent is silent. While the agent is audible,
//   or just after, the microphone mostly hears the agent's own voice (echo); that and
//   the caller talking over the agent never start a turn.
// - The caller must speak for at least `minSpeechMs` to start a turn, so a click or a
//   cough does not.
// - The agent's next onset after a pause of `agentGapMs` ends the turn. The latency is
//   from the caller's last speech to that onset. Below `minLatencyMs` no pipeline can
//   have answered: the agent started while the caller was still talking. That is
//   reported as an overlap, not a latency.
// - If the agent never answers within `unansweredMs` after the caller stops, the turn
//   is unanswered (once), then disarmed until the caller speaks again.

export const DEFAULTS = Object.freeze({
  youSpeaking: 0.02, // caller RMS above this is speech
  agentSpeaking: 0.01, // agent RMS above this is speech
  agentAudible: 0.003, // agent RMS above this can echo into the microphone
  echoTailMs: 250, // caller speech this soon after agent sound is treated as echo
  agentGapMs: 700, // agent silence that separates two answers
  minSpeechMs: 250, // shortest caller speech that starts a turn
  speechGapMs: 600, // caller silence that ends one stretch of speech
  minLatencyMs: 150, // faster than this is an overlap, not an answer
  unansweredMs: 8000, // caller silence without an answer ends the turn this way
});

const OVERLAP_SECS = 0.15;

/** Same inclusive p-quantile as Python statistics.quantiles(..., n=100, method="inclusive"). */
export function pct(values, q) {
  if (!values.length) return null;
  if (values.length === 1) return values[0];
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  const k = (q * (n - 1)) / 100 + 1;
  const f = Math.floor(k);
  const c = Math.ceil(k);
  if (f === c) return sorted[f - 1];
  const weight = k - f;
  return sorted[f - 1] * (1 - weight) + sorted[c - 1] * weight;
}

export function summarise(events) {
  const answered = [];
  let overlapped = 0;
  let unanswered = 0;
  for (const e of events) {
    if (e.kind === "unanswered") {
      unanswered += 1;
      continue;
    }
    const secs = e.ms / 1000;
    if (e.kind === "overlap" || secs < OVERLAP_SECS) {
      overlapped += 1;
    } else {
      answered.push(secs);
    }
  }
  const denom = answered.length + overlapped;
  return {
    answered,
    overlapped,
    unanswered,
    p50: pct(answered, 50),
    p90: pct(answered, 90),
    overlapRate: denom ? (100 * overlapped) / denom : null,
  };
}

export function formatSummary(summary, kind = "heard") {
  const p50 = summary.p50 == null ? "-" : `${(summary.p50 * 1000).toFixed(0)}`;
  const p90 = summary.p90 == null ? "-" : `${(summary.p90 * 1000).toFixed(0)}`;
  const overlapS =
    summary.overlapRate == null ? "-" : `${Math.round(summary.overlapRate)}%`;
  let line = `${kind}: p50 ${p50} / p90 ${p90} ms (n = ${summary.answered.length} answered, overlap ${overlapS}, ${summary.unanswered} unanswered)`;
  if (kind === "server") {
    line += " (overlap not measured at the server)";
  }
  return line;
}

export class TurnMeter {
  constructor(options = {}) {
    this.o = { ...DEFAULTS, ...options };
    this.greetingStarted = false;
    this.greeted = false;
    this.lastAgentLoud = -Infinity;
    this.lastAgentAudible = -Infinity;
    this.speechStart = null;
    this.lastYouLoud = -Infinity;
    this.armed = false;
  }

  /** One tick. Returns {kind: "turn" | "overlap" | "unanswered", ms?} when a turn ends, else null. */
  update(now, you, agent) {
    const o = this.o;
    if (agent > o.agentAudible) this.lastAgentAudible = now;

    // The greeting ends at the agent's first pause.
    if (this.greetingStarted && !this.greeted && now - this.lastAgentLoud > o.agentGapMs) {
      this.greeted = true;
    }

    const agentQuiet = now - this.lastAgentAudible > o.echoTailMs;
    if (you > o.youSpeaking && agentQuiet && this.greeted) {
      if (this.speechStart === null || now - this.lastYouLoud > o.speechGapMs) {
        this.speechStart = now;
      }
      this.lastYouLoud = now;
      if (now - this.speechStart >= o.minSpeechMs) this.armed = true;
    }

    let event = null;
    if (agent > o.agentSpeaking) {
      const onset = now - this.lastAgentLoud > o.agentGapMs;
      if (onset && !this.greetingStarted) {
        this.greetingStarted = true;
      } else if (onset && this.armed) {
        const ms = now - this.lastYouLoud;
        event = { kind: ms < o.minLatencyMs ? "overlap" : "turn", ms };
        this.armed = false;
        this.speechStart = null;
      }
      this.lastAgentLoud = now;
    }
    // Checked after the agent: an answer that starts on this very tick still counts.
    if (!event && this.armed && this.greeted && now - this.lastYouLoud >= o.unansweredMs) {
      event = { kind: "unanswered" };
      this.armed = false;
      this.speechStart = null;
    }
    return event;
  }
}

/** The median: the middle value, or the mean of the two middle values. */
export function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
