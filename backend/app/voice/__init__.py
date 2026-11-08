"""Voice layers. VOICE picks one: each is a module app/voice/<name>.py exposing

    async def run_call(transport: BaseTransport, session: CallSession) -> None

The contract every layer keeps:
- greet the caller with `session.greeting`; use `session.instructions()` as the prompt;
- log what each side said with `session.log("transcript", role="caller" | "agent", text=...)`,
  and what the call consumed with `session.log("usage", ...)` (scripts/call_costs.py prices it);
- return when the caller hangs up or the call is cut. Never call `session.finish()`:
  the server does, after the layer returns.

Layers: `cascade` (option B: Deepgram -> Claude -> Kokoro), the speech-to-speech
options `gemini` (A1: Gemini Live) and `openai` (A2: OpenAI GPT-Live-1, not built
yet), and `none` (silent stub for plumbing tests). app/voice/common.py has the
plumbing they share.
"""

import importlib

from pipecat.transports.base_transport import BaseTransport

from app import config
from app.session import CallSession

LAYERS = ("cascade", "gemini", "openai", "none")


def load(name: str):
    if name not in LAYERS:
        raise ValueError(f"unknown VOICE={name!r}; expected one of {', '.join(LAYERS)}")
    return importlib.import_module(f"app.voice.{name}")


async def run_call(
    transport: BaseTransport, session: CallSession, voice: str | None = None
) -> None:
    await load(voice or config.VOICE).run_call(transport, session)
