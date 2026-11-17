"""Scripted caller for latency and cost measurements over the real voice pipeline.

    uv run python -m scripts.voice_bench --calls 10 --port 7860 --max-usd 0.40

Patient lines are voiced locally with Kokoro (a different voice from the agent's) and
played into a WebRTC call exactly as a browser would send them. For each turn the
caller measures the time from the end of its own speech to the first agent audio it
receives, and reports it to the server, which logs it next to its own measurement.
Stops before a call would take the measured calls' spend past --max-usd.

Works with every VOICE= (it only talks WebRTC to the server), and saves each call as a
stereo WAV, printing its path:

    recordings/bench/<voice>/<call_id>.wav    left: the caller, right: the agent

Both channels are laid on one clock, the caller's: each 20 ms frame sits at the moment
it left the caller or reached it. Silences, slow answers and overlaps sound as they
happened.
"""

import argparse
import asyncio
import fractions
import hashlib
import json
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from app import config
from app.latency_report import summarise
from scripts import call_costs

RATE = 48_000
FRAME = 960  # 20 ms
VOICED = 300  # int16 RMS above which a received frame counts as agent speech
AGENT_DONE_SECS = 1.5  # agent silence that ends its answer
ANSWER_TIMEOUT_SECS = 12

# Each call plays one of these conversations, line by line.
CONVERSATIONS = [
    [
        "Hola, buenas tardes. Quería pedir cita para una limpieza.",
        "Mejor por la mañana, si es posible.",
        "¿Y cuánto suele costar?",
        "Vale, perfecto. Muchas gracias, adiós.",
    ],
    [
        "Hola, mire, llevo dos días con dolor en una muela.",
        "No, fiebre no tengo, pero me molesta bastante al masticar.",
        "¿Me podrían ver esta semana?",
        "De acuerdo, gracias.",
    ],
    [
        "Buenos días. ¿A qué hora abren los sábados?",
        "¿Y aceptan seguro privado?",
        "Una última cosa, ¿hay aparcamiento cerca?",
        "Genial, gracias por todo.",
    ],
]
CALLER_VOICES = ["em_alex", "em_santa"]
CACHE = config.ROOT / "data" / "bench"
RECORDINGS = config.ROOT / "recordings" / "bench"
WAV_RATE = 24_000
# Frames that arrive this much early (network jitter) still play right after the one
# before; anything earlier than that is placed at its own time.
MAX_SLIP_SECS = 0.1
# Paid-tier cost of one ~1.5 min scripted call, for --max-usd, by voice layer.
EXPECTED_USD_PER_CALL = {"cascade": 0.0093 * 1.5, "gemini": 0.05}


class Tape:
    """One side of the call: audio chunks with the monotonic time each crossed the caller."""

    def __init__(self) -> None:
        self.chunks: list[tuple[float, np.ndarray]] = []

    def add(self, at: float, pcm: np.ndarray) -> None:
        self.chunks.append((at, pcm))


def lay_out(
    chunks: list[tuple[float, np.ndarray]], t0: float, rate: int, max_slip: float = MAX_SLIP_SECS
) -> np.ndarray:
    """Place mono int16 chunks on a timeline starting at t0 (silence between them).

    A chunk starts at its own time, unless it arrived up to max_slip early, in which
    case it follows the previous one: jitter must not cut audio short.
    """
    placed: list[tuple[int, np.ndarray]] = []
    end = 0
    for at, pcm in chunks:
        start = max(round((at - t0) * rate), 0)
        if end - round(max_slip * rate) <= start < end:
            start = end
        placed.append((start, pcm))
        end = start + len(pcm)
    out = np.zeros(max((s + len(p) for s, p in placed), default=0), np.int16)
    for start, pcm in placed:
        out[start : start + len(pcm)] = pcm
    return out


def to_rate(pcm: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or not len(pcm):
        return pcm
    n = round(len(pcm) * target / rate)
    return np.interp(np.arange(n) * rate / target, np.arange(len(pcm)), pcm).astype(np.int16)


def save_stereo(path: Path, caller: Tape, agent: Tape, rate: int = WAV_RATE) -> Path:
    """Left: what the caller sent. Right: what it heard. One timeline for both."""
    t0 = min((c[0] for c in caller.chunks + agent.chunks), default=0.0)
    left = lay_out([(t, to_rate(p, RATE, rate)) for t, p in caller.chunks], t0, rate)
    right = lay_out([(t, to_rate(p, RATE, rate)) for t, p in agent.chunks], t0, rate)
    frames = np.zeros((max(len(left), len(right)), 2), np.int16)
    frames[: len(left), 0] = left
    frames[: len(right), 1] = right
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames.tobytes())
    return path


def mono_48k(frame: AudioFrame) -> np.ndarray:
    """A received frame as mono int16 at RATE, whatever its layout and rate."""
    pcm = frame.to_ndarray().reshape(-1)
    channels = len(frame.layout.channels)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return to_rate(pcm.astype(np.int16), frame.sample_rate, RATE)


def voice_lines() -> dict[tuple[str, str], np.ndarray]:
    """Kokoro renders each line once per caller voice; cached as 48 kHz int16 .npy."""
    CACHE.mkdir(parents=True, exist_ok=True)
    lines = {}
    kokoro = None
    for voice in CALLER_VOICES:
        for conversation in CONVERSATIONS:
            for text in conversation:
                digest = hashlib.sha1(text.encode()).hexdigest()[:10]
                path = CACHE / f"{voice}-{digest}.npy"
                if not path.exists():
                    if kokoro is None:
                        from kokoro_onnx import Kokoro
                        from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR

                        kokoro = Kokoro(
                            str(KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"),
                            str(KOKORO_CACHE_DIR / "voices-v1.0.bin"),
                        )
                    samples, sr = kokoro.create(text, voice=voice, lang="es")
                    pcm = np.interp(
                        np.arange(0, len(samples), sr / RATE), np.arange(len(samples)), samples
                    )
                    voiced = np.flatnonzero(np.abs(pcm) > 0.02)
                    pcm = pcm[voiced[0] : voiced[-1] + 1]  # trim leading/trailing silence
                    np.save(path, (pcm * 32767).astype(np.int16))
                lines[(voice, text)] = np.load(path)
    return lines


class CallerMic(MediaStreamTrack):
    """Sends silence, or the line being spoken, in real-time 20 ms frames."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._pcm = np.zeros(0, np.int16)
        self._pos = 0
        self._done: asyncio.Future | None = None
        self._start: float | None = None
        self._sent = 0
        self.tape = Tape()

    def say(self, pcm: np.ndarray) -> asyncio.Future:
        self._pcm, self._pos = pcm, 0
        self._done = asyncio.get_running_loop().create_future()
        return self._done

    async def recv(self) -> AudioFrame:
        if self._start is None:
            self._start = time.monotonic()
        due = self._start + self._sent * FRAME / RATE
        await asyncio.sleep(max(0, due - time.monotonic()))
        chunk = np.zeros(FRAME, np.int16)
        if self._pos < len(self._pcm):
            part = self._pcm[self._pos : self._pos + FRAME]
            chunk[: len(part)] = part
            self._pos += FRAME
            if self._pos >= len(self._pcm) and self._done and not self._done.done():
                self._done.set_result(time.monotonic())  # last voiced sample leaves now
        self.tape.add(time.monotonic(), chunk)
        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self._sent * FRAME
        frame.time_base = fractions.Fraction(1, RATE)
        self._sent += 1
        return frame


class AgentEar:
    """Watches the agent's audio: when speech starts and how long it has been quiet."""

    def __init__(self):
        self.last_voiced = 0.0
        self.onset = asyncio.Event()
        self.onset_at = 0.0
        self._quiet = True
        self.tape = Tape()

    async def listen(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            now = time.monotonic()
            mono = mono_48k(frame)
            self.tape.add(now, mono)
            pcm = mono.astype(np.float32)
            if np.sqrt(np.mean(pcm * pcm)) > VOICED:
                if self._quiet and now - self.last_voiced > AGENT_DONE_SECS:
                    self.onset_at = now
                    self.onset.set()
                self._quiet = False
                self.last_voiced = now
            elif now - self.last_voiced > AGENT_DONE_SECS:
                self._quiet = True

    async def answer_finished(self) -> None:
        while not self._quiet or time.monotonic() - self.last_voiced < AGENT_DONE_SECS:
            await asyncio.sleep(0.05)


def post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(port: int, path: str) -> dict:
    return json.loads(urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=10).read())


async def one_call(
    port: int, voice: str, lines: list[np.ndarray]
) -> tuple[str, list[float | None], Path]:
    pc = RTCPeerConnection()
    mic, ear = CallerMic(), AgentEar()
    pc.addTransceiver(mic, direction="sendrecv")
    pc.addTransceiver("video", direction="sendrecv")
    channel = pc.createDataChannel("control")
    listening: list[asyncio.Task] = []

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            listening.append(asyncio.create_task(ear.listen(track)))

    await pc.setLocalDescription(await pc.createOffer())
    answer = await asyncio.to_thread(
        post, port, "/api/offer", {"sdp": pc.localDescription.sdp, "type": "offer"}
    )
    await pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], answer["type"]))

    async def keepalive():
        while True:
            if channel.readyState == "open":
                channel.send("ping")
            await asyncio.sleep(1)

    pinger = asyncio.create_task(keepalive())
    latencies: list[float | None] = []
    turns: list[dict] = []
    try:
        try:
            await asyncio.wait_for(ear.onset.wait(), ANSWER_TIMEOUT_SECS)  # the greeting
            await ear.answer_finished()
        except TimeoutError:
            print("  no greeting within the timeout")
        for pcm in lines:
            ear.onset.clear()
            ended = await mic.say(pcm)
            try:
                await asyncio.wait_for(ear.onset.wait(), ANSWER_TIMEOUT_SECS)
            except TimeoutError:
                print("  no answer within the timeout")
                turns.append({"spoke_until": round(ended, 3), "heard_at": None})
                latencies.append(None)
                continue
            secs = ear.onset_at - ended
            latencies.append(secs)
            turns.append({"spoke_until": round(ended, 3), "heard_at": round(ear.onset_at, 3)})
            await asyncio.to_thread(
                post, port, "/api/heard", {"pc_id": answer["pc_id"], "secs": secs}
            )
            await ear.answer_finished()
    finally:
        pinger.cancel()
        await pc.close()
        for task in listening:
            task.cancel()
    call_id = answer["call_id"]
    (CACHE / f"{call_id}.turns.json").write_text(json.dumps(turns))
    wav = save_stereo(RECORDINGS / voice / f"{call_id}.wav", mic.tape, ear.tape)
    return call_id, latencies, wav


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=3)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--max-usd", type=float, default=0.40)
    args = parser.parse_args()

    voice_layer = (await asyncio.to_thread(get, args.port, "/health"))["voice"]
    expected = EXPECTED_USD_PER_CALL.get(voice_layer, 0.0)
    lines = voice_lines()
    spent, done, all_latencies = 0.0, [], []
    for i in range(args.calls):
        voice = CALLER_VOICES[i % len(CALLER_VOICES)]
        conversation = CONVERSATIONS[i % len(CONVERSATIONS)]
        pcms = [lines[(voice, text)] for text in conversation]
        if spent + expected > args.max_usd:
            print(f"stopping: ${spent:.4f} spent, the next call could pass ${args.max_usd}")
            break
        call_id, latencies, wav = await one_call(args.port, voice_layer, pcms)
        await asyncio.sleep(3)  # let the server close the call's log
        r = call_costs.report(Path(config.CALLS_DIR) / f"{call_id}.jsonl")
        spent += r.usd
        done.append(call_id)
        all_latencies.extend(latencies)
        call_line = summarise(latencies).format("heard")
        per_turn = ", ".join("-" if v is None else f"{v * 1000:.0f}" for v in latencies)
        print(f"call {i + 1} {call_id}: {r.secs:.0f}s, ${r.usd:.4f}, {call_line}  [{per_turn} ms]")
        print(f"  recording: {wav.relative_to(config.ROOT)}")
    total_line = summarise(all_latencies).format("heard")
    print(f"VOICE={voice_layer}: {len(done)} calls, ${spent:.4f} at paid-tier rates, {total_line}")


if __name__ == "__main__":
    asyncio.run(main())
