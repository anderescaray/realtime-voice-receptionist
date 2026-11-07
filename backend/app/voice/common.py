"""Plumbing every voice layer shares: the call log, latency, usage, the call cap.

A layer builds its own pipeline and greeting, then hands them to `run_pipeline`, which
logs both sides' transcripts, each turn's latency and what the call consumed, cuts the
call at MAX_CALL_SECS and returns when the caller hangs up.
"""

import asyncio
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from app import config
from app.session import CallSession


class UsageLogger(BaseObserver):
    """Logs each model's usage metrics once, as `usage` events in the call log.

    `service` names the bucket scripts/call_costs.py prices them in: "llm" for the
    cascade's text model, "s2s" for a speech-to-speech model (tokens split by modality).
    """

    def __init__(self, session: CallSession, *, service: str, model: str):
        super().__init__()
        self._session = session
        self._service = service
        self._model = model

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push or not isinstance(data.frame, MetricsFrame):
            return
        for item in data.frame.data:
            if isinstance(item, LLMUsageMetricsData):
                u = item.value
                audio = {
                    "input_audio_tokens": u.input_audio_tokens,
                    "output_audio_tokens": u.output_audio_tokens,
                }
                self._session.log(
                    "usage",
                    service=self._service,
                    model=self._model,
                    input_tokens=u.prompt_tokens,
                    output_tokens=u.completion_tokens,
                    cache_read_tokens=u.cache_read_input_tokens or 0,
                    cache_write_tokens=u.cache_creation_input_tokens or 0,
                    **{k: v for k, v in audio.items() if v is not None},
                )
            elif isinstance(item, TTSUsageMetricsData):
                self._session.log(
                    "usage", service="tts", model="kokoro-local", characters=item.value
                )


def latency_observer(session: CallSession) -> UserBotLatencyObserver:
    """Logs the greeting's latency, each turn's latency and its per-stage breakdown."""
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

    return latency


async def run_pipeline(
    transport: BaseTransport,
    session: CallSession,
    pipeline: Pipeline,
    aggregators: LLMContextAggregatorPair,
    *,
    greet: Callable[[PipelineWorker], Awaitable[None]],
    usage: UsageLogger,
    params: PipelineParams | None = None,
) -> None:
    params = params or PipelineParams()
    params.enable_metrics = True
    params.enable_usage_metrics = True
    worker = PipelineWorker(
        pipeline,
        params=params,
        observers=[latency_observer(session), usage],
        enable_rtvi=False,
    )

    @aggregators.user().event_handler("on_user_turn_message_added")
    async def on_caller_said(aggregator, message):
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
        await greet(worker)

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
