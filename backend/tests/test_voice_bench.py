"""The bench's stereo recording: both sides on the caller's clock."""

import wave

import numpy as np

from scripts.voice_bench import Tape, lay_out, save_stereo

RATE = 1000  # small numbers keep the arithmetic readable


def tone(n: int, value: int = 1000) -> np.ndarray:
    return np.full(n, value, np.int16)


def test_chunks_sit_at_their_own_time_with_silence_between():
    out = lay_out([(10.0, tone(20)), (10.5, tone(20, 2000))], t0=10.0, rate=RATE)
    assert len(out) == 520
    assert (out[:20] == 1000).all()
    assert (out[20:500] == 0).all()  # the gap is kept, not squeezed out
    assert (out[500:] == 2000).all()


def test_a_slightly_early_chunk_follows_the_previous_one():
    # The second chunk arrives 10 ms early (jitter): it must not overwrite the first.
    out = lay_out([(0.0, tone(20, 1)), (0.01, tone(20, 2))], t0=0.0, rate=RATE)
    assert list(out[:20]) == [1] * 20 and list(out[20:40]) == [2] * 20


def test_stereo_file_keeps_overlap_between_caller_and_agent(tmp_path):
    caller, agent = Tape(), Tape()
    for i in range(50):  # caller speaks 0.0-1.0 s
        caller.add(100.0 + i * 0.02, tone(960, 3000))
    for i in range(50):  # agent starts at 0.8 s, over the caller's last 0.2 s
        agent.add(100.8 + i * 0.02, tone(960, -3000))
    path = save_stereo(tmp_path / "call.wav", caller, agent, rate=8000)
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 8000)
        frames = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2)
    left, right = frames[:, 0], frames[:, 1]
    at = lambda secs: round(secs * 8000)  # noqa: E731
    assert left[at(0.5)] == 3000 and right[at(0.5)] == 0  # only the caller
    assert left[at(0.9)] == 3000 and right[at(0.9)] == -3000  # both: an overlap
    assert left[at(1.5)] == 0 and right[at(1.5)] == -3000  # only the agent
