"""VOICE=gemini: option A1, Gemini Live as a speech-to-speech model.

    caller audio -> Gemini Live (hears, thinks, speaks) -> caller

One model does the whole turn over a WebSocket: Google's server-side VAD decides when
the caller has finished, and the model answers in its own voice. A local Silero VAD
runs alongside it only to mark the caller's turns, so latency is measured the same
way as in the cascade; Gemini's own turn-taking is untouched.

Costs: usage arrives as tokens split by modality (audio and text, in and out), logged
as service "s2s". On the Gemini API free tier the call is not billed, and Google may
use what is sent to improve its products: scripted callers only, never patient data.

No tools and no brain hand-off yet (PLAN.md, "Voice layers and brain hand-off").
"""

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport

from app import config
from app.session import CallSession
from app.voice.common import UsageLogger, run_pipeline

# Gemini Live takes 16 kHz audio in and speaks at 24 kHz.
IN_RATE, OUT_RATE = 16_000, 24_000


def greeting_cue(greeting: str) -> str:
    """The first user turn: the model has nothing to answer yet, so it is told to greet."""
    return (
        "La llamada acaba de conectar y el paciente todavía no ha dicho nada. "
        f"Salúdale diciendo exactamente esto y espera su respuesta: «{greeting}»"
    )


def make_llm(session: CallSession) -> GeminiLiveLLMService:
    if not config.GOOGLE_API_KEY:
        raise RuntimeError("VOICE=gemini needs GOOGLE_API_KEY in .env")
    return GeminiLiveLLMService(
        api_key=config.GOOGLE_API_KEY,
        settings=GeminiLiveLLMService.Settings(
            model=f"models/{config.GEMINI_MODEL}",
            voice=config.GEMINI_VOICE,
            language=Language.ES,
            system_instruction=session.instructions(),
        ),
    )


async def run_call(transport: BaseTransport, session: CallSession) -> None:
    session.log(
        "voice_config",
        voice="gemini",
        s2s=config.GEMINI_MODEL,
        s2s_voice=config.GEMINI_VOICE,
    )
    llm = make_llm(session)
    context = LLMContext(messages=[{"role": "user", "content": greeting_cue(session.greeting)}])
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [
            transport.input(),
            aggregators.user(),
            llm,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    async def greet(worker: PipelineWorker) -> None:
        await worker.queue_frame(LLMRunFrame())

    await run_pipeline(
        transport,
        session,
        pipeline,
        aggregators,
        greet=greet,
        usage=UsageLogger(session, service="s2s", model=config.GEMINI_MODEL),
        params=PipelineParams(audio_in_sample_rate=IN_RATE, audio_out_sample_rate=OUT_RATE),
    )
