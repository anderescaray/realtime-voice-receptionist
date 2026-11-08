"""VOICE=cascade: option B. Deepgram transcribes, Claude answers, Kokoro speaks locally.

    caller audio -> VAD -> Deepgram STT -> Claude (Haiku) -> Kokoro TTS -> caller

Every turn logs `latency`: seconds from when the caller actually stopped talking
(the VAD's stop time minus its silence window) to the first agent audio leaving
the server, with a per-stage breakdown. Every model response logs `usage`
(app/voice/common.py).
"""

import functools

import pipecat.services.kokoro.tts as kokoro_tts
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport

from app import config
from app.session import CallSession
from app.voice.common import UsageLogger, run_pipeline

# kokoro-onnx loads a ~300 MB model per instance; share one across calls so a call
# does not wait for it. Inference on a shared instance is safe (one ONNX session).
kokoro_tts.Kokoro = functools.cache(kokoro_tts.Kokoro)


def make_stt():
    model = config.DEEPGRAM_MODEL
    if model.startswith("flux"):
        return DeepgramFluxSTTService(
            api_key=config.DEEPGRAM_API_KEY,
            settings=DeepgramFluxSTTService.Settings(
                model=model, language_hints=[Language.ES, Language.EN]
            ),
        )
    return DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        settings=DeepgramSTTService.Settings(
            model=model, language=config.DEEPGRAM_LANGUAGE, smart_format=True
        ),
    )


def make_tts() -> KokoroTTSService:
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=config.KOKORO_VOICE, language=Language.ES)
    )


def warm_up() -> None:
    """Download and load the Kokoro model before the first call (blocking)."""
    make_tts()


async def run_call(transport: BaseTransport, session: CallSession) -> None:
    session.log(
        "voice_config",
        voice="cascade",
        stt=config.DEEPGRAM_MODEL,
        stt_language="multi"
        if config.DEEPGRAM_MODEL.startswith("flux")
        else config.DEEPGRAM_LANGUAGE,
        llm=config.ANTHROPIC_MODEL,
        tts=f"kokoro:{config.KOKORO_VOICE}",
    )
    stt = make_stt()
    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        settings=AnthropicLLMService.Settings(
            model=config.ANTHROPIC_MODEL,
            system_instruction=session.instructions(),
            max_tokens=300,
        ),
    )
    tts = make_tts()

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    async def greet(worker: PipelineWorker) -> None:
        await worker.queue_frame(TTSSpeakFrame(session.greeting))

    await run_pipeline(
        transport,
        session,
        pipeline,
        aggregators,
        greet=greet,
        usage=UsageLogger(session, service="llm", model=config.ANTHROPIC_MODEL),
    )
