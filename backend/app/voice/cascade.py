"""VOICE=cascade: option B. Deepgram transcribes, Claude answers, Kokoro speaks locally.

    caller audio -> VAD -> Deepgram STT -> Claude (Haiku) -> Kokoro TTS -> caller

Every turn logs `latency`: seconds from when the caller actually stopped talking
(the VAD's stop time minus its silence window) to the first agent audio leaving
the server, with a per-stage breakdown. Every model response logs `usage`.
"""

import asyncio
import functools

import pipecat.services.kokoro.tts as kokoro_tts
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import MetricsFrame, TTSSpeakFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
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
from pipecat.workers.runner import WorkerRunner

from app import config
from app.session import CallSession

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


class UsageLogger(BaseObserver):
    """Logs each service's usage metrics once, as `usage` events in the call log."""

    def __init__(self, session: CallSession):
        super().__init__()
        self._session = session

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push or not isinstance(data.frame, MetricsFrame):
            return
        for item in data.frame.data:
            if isinstance(item, LLMUsageMetricsData):
                u = item.value
                self._session.log(
                    "usage",
                    service="llm",
                    model=item.model or config.ANTHROPIC_MODEL,
                    input_tokens=u.prompt_tokens,
                    output_tokens=u.completion_tokens,
                    cache_read_tokens=u.cache_read_input_tokens or 0,
                    cache_write_tokens=u.cache_creation_input_tokens or 0,
                )
            elif isinstance(item, TTSUsageMetricsData):
                self._session.log(
                    "usage", service="tts", model="kokoro-local", characters=item.value
                )


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

    latency = UserBotLatencyObserver()

    @latency.event_handler("on_first_bot_speech_latency")
    async def on_greeting(observer, secs: float):
        session.log("greeting_latency", secs=round(secs, 3))

    @latency.event_handler("on_latency_measured")
    async def on_turn(observer, secs: float):
        session.log("latency", secs=round(secs, 3))

    @latency.event_handler("on_latency_breakdown")
    async def on_breakdown(observer, breakdown):
        session.log(
            "latency_breakdown",
            measured_from=breakdown.measured_from,
            total_secs=round(breakdown.total_secs, 3),
            stages=[
                {"key": c.key, "label": c.label, "secs": round(c.duration_secs, 3)}
                for c in breakdown.contributions
            ],
        )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[latency, UsageLogger(session)],
        enable_rtvi=False,
    )

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def on_caller_said(aggregator, strategy, message):
        if message.content:
            session.log("transcript", role="caller", text=message.content)

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_agent_said(aggregator, message):
        if message.content:
            session.log(
                "transcript", role="agent", text=message.content, interrupted=message.interrupted
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await worker.queue_frame(TTSSpeakFrame(session.greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        session.log("caller_hung_up")
        await worker.cancel()

    async def cut_at_cap():
        await asyncio.sleep(config.MAX_CALL_SECS)
        session.log("call_cap_reached", secs=config.MAX_CALL_SECS)
        await worker.cancel()

    cap = asyncio.create_task(cut_at_cap())
    try:
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
    finally:
        cap.cancel()
