"""VOICE=s2s: option A, a speech-to-speech model. Not built yet.

It will keep the same contract as app/voice/cascade.py so the two can be compared
call for call. Until then it refuses to start rather than call a paid API.
"""

from pipecat.transports.base_transport import BaseTransport

from app.session import CallSession


async def run_call(transport: BaseTransport, session: CallSession) -> None:
    session.log("voice_unavailable", voice="s2s")
    raise NotImplementedError("VOICE=s2s is not built yet; use VOICE=cascade")
