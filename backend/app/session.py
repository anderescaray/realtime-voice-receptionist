"""Per-call state: identity, the call's own clock and its JSONL log.

This is the minimal session the voice test needs. Staging, validation and the
outcome of a call are added on top of it later (PLAN.md, "Tools and call session").
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import config

GREETING_ES = "Hola, gracias por llamar a la clínica dental. ¿En qué le puedo ayudar?"

# Placeholder until the per-call prompt exists (PLAN.md, "Per-call prompt").
VOICE_TEST_INSTRUCTIONS = """\
You answer the phone for a dental clinic. This is an audio test line: you have no
diary and no tools yet, so never promise or confirm an appointment; if the caller
wants one, say that booking by phone is coming soon and offer to answer a question.

Speak Spanish unless the caller speaks English. Everything you write is read aloud
by a speech engine, so reply in one or two short sentences of plain text: no lists,
markdown, emoji or abbreviations. Write numbers, times and dates out in words.
Never give medical advice; for severe pain, swelling with fever or trouble
breathing, tell the caller to call 112.
"""


@dataclass
class CallSession:
    call_id: str
    now: datetime  # the call's clock, fixed when the call starts
    log_path: Path
    greeting: str = GREETING_ES
    started_monotonic: float = field(default_factory=time.monotonic)
    finished: bool = False

    @classmethod
    def start(
        cls,
        *,
        call_id: str | None = None,
        now: datetime | None = None,
        calls_dir: Path | None = None,
        **info: Any,
    ) -> "CallSession":
        call_id = call_id or uuid.uuid4().hex[:12]
        calls_dir = calls_dir or config.CALLS_DIR
        calls_dir.mkdir(parents=True, exist_ok=True)
        session = cls(
            call_id=call_id,
            now=now or datetime.now(UTC),
            log_path=calls_dir / f"{call_id}.jsonl",
        )
        session.log(
            "call_started",
            now=session.now.isoformat(),
            monotonic=round(session.started_monotonic, 3),  # lines up with other local logs
            **info,
        )
        return session

    def instructions(self) -> str:
        return VOICE_TEST_INSTRUCTIONS

    def elapsed(self) -> float:
        return time.monotonic() - self.started_monotonic

    def log(self, kind: str, **data: Any) -> None:
        """Append one event to the call's JSONL log."""
        event = {"t": round(self.elapsed(), 3), "kind": kind, **data}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def finish(self) -> None:
        """Close the call's log once; the server calls it after the voice layer returns."""
        if self.finished:
            return
        self.finished = True
        self.log("call_ended", duration_secs=round(self.elapsed(), 3))
