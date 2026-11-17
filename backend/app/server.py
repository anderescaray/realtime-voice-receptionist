"""Call server: browser calls over WebRTC, one session and one voice pipeline per call.

    cd backend && uv run python -m app.server     # then open http://localhost:7860

Routes:
    GET  /            the test call page (app/static/call.html)
    GET  /static/...  its scripts
    POST /api/offer   WebRTC offer -> answer; starts the call
    POST /api/heard   the page's own measure of response latency, logged with the call
    GET  /health
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pydantic import BaseModel

from app import config, steady_clock, voice
from app.session import CallSession

STATIC = Path(__file__).parent / "static"

# Audio pacing and turn timing must not follow a wall clock that steps.
steady_clock.install()

ACTIVE: dict[str, CallSession] = {}  # by WebRTC peer-connection id
TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.VOICE == "cascade":
        from app.voice import cascade

        logger.info("loading the local Kokoro voice (first run downloads ~330 MB)")
        await asyncio.to_thread(cascade.warm_up)
    logger.info(f"ready: VOICE={config.VOICE} on http://localhost:{config.PORT}")
    yield
    await webrtc.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
webrtc = SmallWebRTCRequestHandler()


async def run_call(connection: SmallWebRTCConnection, session: CallSession) -> None:
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    try:
        await voice.run_call(transport, session)
    except Exception as e:
        logger.exception(f"call {session.call_id}: voice layer failed")
        session.log("voice_error", error=repr(e))
    finally:
        ACTIVE.pop(connection.pc_id, None)
        session.finish()
        logger.info(f"call {session.call_id} ended after {session.elapsed():.1f}s")


@app.get("/")
async def index():
    return FileResponse(STATIC / "call.html")


@app.get("/health")
async def health():
    return {"ok": True, "voice": config.VOICE, "active_calls": len(ACTIVE)}


@app.post("/api/offer")
async def offer(body: dict):
    async def start(connection: SmallWebRTCConnection) -> None:
        session = CallSession.start(voice=config.VOICE, channel="browser")
        ACTIVE[connection.pc_id] = session
        logger.info(f"call {session.call_id} started (pc {connection.pc_id})")
        task = asyncio.create_task(run_call(connection, session))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)

    answer = await webrtc.handle_web_request(SmallWebRTCRequest.from_dict(body), start)
    if answer and (session := ACTIVE.get(answer["pc_id"])):
        answer["call_id"] = session.call_id
    return answer


class Heard(BaseModel):
    pc_id: str
    kind: Literal["turn", "overlap", "unanswered"] = "turn"
    secs: float | None = None


@app.post("/api/heard")
async def heard(body: Heard):
    session = ACTIVE.get(body.pc_id)
    if not session:
        raise HTTPException(404, "no active call")
    if body.kind == "unanswered":
        session.log("heard_unanswered")
    elif body.kind == "overlap":
        if body.secs is None:
            raise HTTPException(422, "secs required for overlap")
        session.log("heard_latency", secs=round(body.secs, 3), overlapped=True)
    else:
        if body.secs is None:
            raise HTTPException(422, "secs required for turn")
        session.log("heard_latency", secs=round(body.secs, 3))
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
