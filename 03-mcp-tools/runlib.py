"""Shared helpers for the experiment scripts: run folders, the recorded setup, and Ollama model loading."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_data import DB_PATH, INJECTED_DB_PATH, content_digest, ensure_dbs
from host import OLLAMA_URL, OPTIONS

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def run_dir(prefix: str, out: Path | None) -> Path:
    path = out or RUNS / f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{OLLAMA_URL}{path}", timeout=10) as r:
        return json.loads(r.read())


def model_digests(models: list[str]) -> dict[str, str | None]:
    tags = {m["name"]: m.get("digest") for m in _get("/api/tags").get("models", [])}
    return {m: tags.get(m) for m in models}


def unload(model: str) -> None:
    """Unload a model (Ollama: a generate request with keep_alive 0 and no prompt)."""
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as r:
        r.read()


def loaded_models() -> list[str]:
    return [m["name"] for m in _get("/api/ps").get("models", [])]


def setup(models: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Everything needed to reproduce a run, recorded next to its results."""
    ensure_dbs()
    info: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": {p: importlib.metadata.version(p) for p in ("mcp", "mcp-types", "pydantic", "anyio", "httpx2",
                                                                "starlette", "uvicorn")},
        "db_digest": content_digest(DB_PATH)[:16],
        "injected_db_digest": content_digest(INJECTED_DB_PATH)[:16],
        "command": " ".join([Path(sys.executable).name, *sys.argv]),
    }
    if models:
        info["ollama_version"] = _get("/api/version").get("version")
        info["models"] = model_digests(models)
        info["ollama_options"] = OPTIONS
        info["other_models_loaded_at_start"] = loaded_models()
    info.update(extra)
    return info


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def table(rows: list[list[Any]], header: list[str]) -> str:
    cells = [header, *[[str(c) for c in r] for r in rows]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
