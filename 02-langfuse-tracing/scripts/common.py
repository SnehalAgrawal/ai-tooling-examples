"""Shared helpers for the measurement scripts: run metadata, output files, the dataset."""

import json
import platform
import sys
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

from fleet.config import LANGFUSE_BASE_URL, OLLAMA_URL

DEMO_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = DEMO_DIR / "runs"
QUESTIONS_FILE = DEMO_DIR / "data" / "questions.json"


def load_questions() -> list[dict[str, Any]]:
    return json.loads(QUESTIONS_FILE.read_text())


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def langfuse_server_version() -> str | None:
    """The server's version from GET /api/public/health (None if it cannot be reached)."""
    try:
        return httpx.get(f"{LANGFUSE_BASE_URL}/api/public/health", timeout=5).json().get("version")
    except (httpx.HTTPError, ValueError):
        return None


def run_metadata(**extra: Any) -> dict[str, Any]:
    meta = {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "langfuse_sdk": version("langfuse"),
        "opentelemetry_sdk": version("opentelemetry-sdk"),
        "langfuse_base_url": LANGFUSE_BASE_URL,
        "langfuse_server_version": langfuse_server_version(),
        "ollama_url": OLLAMA_URL,
        "command": " ".join(sys.argv),
    }
    meta.update(extra)
    return meta


def write_json(name: str, data: Any) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / name
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return path


def ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self.start
        return False
