"""Scripted caller for latency and cost measurements over the real voice pipeline.

    uv run python -m scripts.voice_bench --calls 10 --port 7860 --max-usd 0.40

Patient lines are voiced locally with Kokoro (a different voice from the agent's) and
played into a WebRTC call exactly as a browser would send them. For each turn the
caller measures the time from the end of its own speech to the first agent audio it
receives, and reports it to the server, which logs it next to its own measurement.
Stops before a call would take the measured calls' spend past --max-usd.
"""

import argparse
import asyncio
import fractions
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from app import config
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

    async def listen(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            pcm = frame.to_ndarray().astype(np.float32)
            now = time.monotonic()
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


async def one_call(port: int, lines: list[np.ndarray]) -> tuple[str, list[float]]:
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
    heard: list[float] = []
    turns: list[dict] = []
    try:
        await asyncio.wait_for(ear.onset.wait(), ANSWER_TIMEOUT_SECS)  # the greeting
        await ear.answer_finished()
        for pcm in lines:
            ear.onset.clear()
            ended = await mic.say(pcm)
            try:
                await asyncio.wait_for(ear.onset.wait(), ANSWER_TIMEOUT_SECS)
            except TimeoutError:
                print("  no answer within the timeout")
                continue
            secs = ear.onset_at - ended
            heard.append(secs)
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
    (CACHE / f"{answer['call_id']}.turns.json").write_text(json.dumps(turns))
    return answer["call_id"], heard


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=3)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--max-usd", type=float, default=0.40)
    args = parser.parse_args()

    lines = voice_lines()
    spent, done = 0.0, []
    for i in range(args.calls):
        voice = CALLER_VOICES[i % len(CALLER_VOICES)]
        conversation = CONVERSATIONS[i % len(CONVERSATIONS)]
        pcms = [lines[(voice, text)] for text in conversation]
        expected = 0.0093 * 1.5  # one call of about 1.5 min at the dearest STT rate
        if spent + expected > args.max_usd:
            print(f"stopping: ${spent:.4f} spent, the next call could pass ${args.max_usd}")
            break
        call_id, heard = await one_call(args.port, pcms)
        await asyncio.sleep(3)  # let the server close the call's log
        r = call_costs.report(Path(config.CALLS_DIR) / f"{call_id}.jsonl")
        spent += r.usd
        done.append(call_id)
        ms = ", ".join(f"{h * 1000:.0f}" for h in heard)
        print(f"call {i + 1} {call_id}: {r.secs:.0f}s, ${r.usd:.4f}, heard ms [{ms}]")
    print(f"{len(done)} calls, ${spent:.4f} spent: {' '.join(done)}")


if __name__ == "__main__":
    asyncio.run(main())
