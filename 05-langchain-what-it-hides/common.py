"""Shared helpers: the plain HTTP baseline, run folders, the environment record, and a tracing guard."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from wire import OLLAMA_URL

HERE = Path(__file__).resolve().parent
MODELS = ["qwen2.5:3b", "llama3.2:3b", "llama3.2:1b"]
SAMPLING = {"temperature": 0, "seed": 42, "num_ctx": 8192}

TRACING_VARS = ["LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"]


def refuse_if_tracing() -> None:
    """Stop if any LangSmith tracing variable is set: these runs must not send anything anywhere."""
    on = {k: os.environ[k] for k in TRACING_VARS if os.environ.get(k, "").strip()}
    if on:
        sys.exit(f"LangSmith tracing variables are set ({on}); unset them, these demos send nothing off the machine.")


def run_dir(name: str, out: str | None = None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out) if out else HERE / "runs" / f"{name}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def plain_chat(client: httpx.Client, base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """The baseline: one POST to Ollama's /api/chat with exactly ``body``, not streamed unless asked."""
    r = client.post(f"{base_url}/api/chat", json=body)
    r.raise_for_status()
    if body.get("stream", True):
        lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        final = dict(lines[-1])
        final["message"] = {"role": "assistant", "content": "".join(x["message"].get("content", "") for x in lines)}
        return final
    return r.json()


def stop_models(models: list[str]) -> None:
    """Unload models from Ollama (the machine is shared)."""
    for m in models:
        subprocess.run(["ollama", "stop", m], capture_output=True, check=False)


def environment(models: list[str] | None = None, base_url: str = OLLAMA_URL) -> dict[str, Any]:
    """Versions and, if Ollama is reachable, its version and the digests of the models used."""
    env: dict[str, Any] = {
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": {},
        "tracing_env": {k: os.environ.get(k) for k in TRACING_VARS},
    }
    for p in ["langchain", "langchain-core", "langchain-ollama", "ollama", "httpx", "pydantic", "langsmith"]:
        try:
            env["packages"][p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            env["packages"][p] = None
    try:
        with httpx.Client(timeout=5) as c:
            env["ollama_version"] = c.get(f"{base_url}/api/version").json().get("version")
            tags = c.get(f"{base_url}/api/tags").json().get("models", [])
        env["models"] = {t["name"]: t.get("digest") for t in tags if not models or t["name"] in models}
    except (httpx.HTTPError, ValueError):
        env["ollama_version"] = None
    return env
