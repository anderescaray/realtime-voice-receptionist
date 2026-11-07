"""VOICE=openai: option A2, OpenAI GPT-Live-1 as a speech-to-speech model. Not built yet.

It will keep the same contract as app/voice/gemini.py (option A1) so the two can be
compared call for call. Until then it refuses to start rather than call a paid API.
"""

from pipecat.transports.base_transport import BaseTransport

from app.session import CallSession


async def run_call(transport: BaseTransport, session: CallSession) -> None:
    session.log("voice_unavailable", voice="openai")
    raise NotImplementedError("VOICE=openai is not built yet; use VOICE=gemini or cascade")
