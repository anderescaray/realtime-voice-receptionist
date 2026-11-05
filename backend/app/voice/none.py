"""VOICE=none: wires the caller's audio in and out and says nothing.

For plumbing tests only (connect, log, hang up). A real caller hears silence.
"""

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from app.session import CallSession


async def run_call(transport: BaseTransport, session: CallSession) -> None:
    worker = PipelineWorker(Pipeline([transport.input(), transport.output()]), enable_rtvi=False)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        session.log("caller_hung_up")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
