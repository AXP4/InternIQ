"""Settings loaded from .env, plus the model names used everywhere."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Models: change them here only.
SCREEN_MODEL = "claude-haiku-5-5"  # screening and short extraction
WRITE_MODEL = "claude-sonnet-5-5"  # cover letters and form filling


def require(name: str) -> str:
    """Return a .env value, or stop with a clear message if it's missing."""
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name} in .env (see .env.example)")
    return value


def channel_id(name: str) -> int:
    value = require(name)
    if not value.isdigit():
        raise SystemExit(f"{name} in .env must be a number (right click the channel > Copy Channel ID)")
    return int(value)
