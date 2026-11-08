"""A wall clock that never steps, for the Pipecat modules that time durations with it.

Pipecat's WebRTC transport paces outgoing audio, checks the caller's heartbeat and
times speech and turn ends with `time.time()`. When the machine's wall clock steps,
those durations go wrong: on a WSL2 dev box whose clock was being stepped by +12.6 s
for a tenth of a second every few seconds, the transport burst queued agent audio
out and dropped it, leaving holes of a second or more mid-sentence, and took a live
caller for gone.

`install()` gives those modules a `time` whose `time()` moves only with the monotonic
clock (anchored to the wall clock once, at install). Every other function is the
real one. On a server with a steady clock this changes nothing.
"""

import importlib
import time
import types

# Modules that measure durations with time.time(), checked against pipecat 1.15.0.
MODULES = (
    "pipecat.transports.smallwebrtc.transport",
    "pipecat.transports.smallwebrtc.connection",
    "pipecat.transports.base_output",
    "pipecat.audio.vad.vad_controller",
    "pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy",
    "pipecat.turns.user_stop.classifier_user_turn_stop_strategy",
)


def steady_time_module(anchor: float | None = None) -> types.ModuleType:
    """A stand-in for the `time` module whose `time()` follows the monotonic clock."""
    offset = (time.time() if anchor is None else anchor) - time.monotonic()
    steady = types.ModuleType("time")
    steady.__dict__.update({k: v for k, v in vars(time).items() if not k.startswith("__")})
    steady.time = lambda: offset + time.monotonic()  # type: ignore[attr-defined]
    return steady


def install() -> None:
    steady = steady_time_module()
    for name in MODULES:
        module = importlib.import_module(name)
        if getattr(module, "time", None) is time:
            module.time = steady  # type: ignore[attr-defined]
