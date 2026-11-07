"""Settings, read from the environment and from the repository's .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Which voice layer runs a call: a module name under app/voice/.
VOICE = os.getenv("VOICE", "cascade")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7860"))

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Cascade (option B). Flux models detect the end of the turn themselves;
# Nova models rely on the local turn model instead.
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "flux-general-multi")
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "multi")  # Nova only
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "ef_dora")

# Speech-to-speech, option A1 (Gemini Live on the Gemini API).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-live")
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Kore")

# One JSONL file per call.
CALLS_DIR = Path(os.getenv("CALLS_DIR", str(ROOT / "data" / "calls")))

# Hard cap on a call's length, in seconds.
MAX_CALL_SECS = int(os.getenv("MAX_CALL_SECS", "300"))
