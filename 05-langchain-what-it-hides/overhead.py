"""Measurement 3: what LangChain costs in time and on disk, with the model taken out of the picture.

1. Per-call time against the stub (an instant fake /api/chat on 127.0.0.1, no proxy), interleaved
   round by round so drift hits every client equally: plain httpx (not streamed, and streamed), the
   ollama Python client, ChatOllama.invoke(), ChatOllama.stream(), and structured output
   (with_structured_output vs httpx plus Pydantic validation).
2. Import time, each in a fresh interpreter, repeated; plus a ``-X importtime`` breakdown.
3. Install footprint: the dependency closure of each entry point, from installed package metadata.

    python overhead.py                 # N=500: about a minute
    python overhead.py -n 50 --import-repeats 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from importlib import metadata
from typing import Any

import httpx
import ollama
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import fleet
from analysis import percentile
from common import environment, refuse_if_tracing, run_dir, write_json
from wire import StubOllama

GOOD_JSON = json.dumps({"package_id": "PKG-10432", "city": "Leeds", "reason": "vehicle_breakdown", "delay_hours": 4})


# --------------------------------------------------------------------------- per-call time


def call_clients(url: str) -> dict[str, Callable[[], Any]]:
    msgs = fleet.chat_messages()
    lc_msgs = [SystemMessage(msgs[0]["content"]), HumanMessage(msgs[1]["content"])]
    hx = httpx.Client()
    oc = ollama.Client(host=url)
    llm = ChatOllama(model="stub-model", base_url=url)
    structured = llm.with_structured_output(fleet.DeliveryException)
    x_msgs = fleet.extraction_messages(fleet.CASES[0][1])
    schema = fleet.DeliveryException.model_json_schema()

    def plain_nostream():
        return hx.post(f"{url}/api/chat", json={"model": "stub-model", "messages": msgs, "stream": False}).json()

    def plain_stream():
        with hx.stream("POST", f"{url}/api/chat", json={"model": "stub-model", "messages": msgs}) as r:
            return [json.loads(line) for line in r.iter_lines() if line]

    def ollama_client():
        return oc.chat(model="stub-model", messages=msgs, stream=False)

    def plain_structured():
        body = {"model": "stub-model", "messages": x_msgs, "stream": False, "format": schema}
        content = hx.post(f"{url}/api/chat", json=body).json()["message"]["content"]
        return fleet.DeliveryException.model_validate_json(content)

    return {
        "plain_httpx": plain_nostream,
        "plain_httpx_streamed": plain_stream,
        "ollama_client": ollama_client,
        "langchain_invoke": lambda: llm.invoke(lc_msgs),
        "langchain_stream": lambda: list(llm.stream(lc_msgs)),
        "plain_structured": plain_structured,
        "langchain_structured": lambda: structured.invoke(x_msgs),
    }


def time_calls(n: int, warmup: int) -> dict[str, Any]:
    with StubOllama(content=GOOD_JSON) as stub:
        stub.keep_bodies = False
        clients = call_clients(stub.url)
        for _ in range(warmup):
            for f in clients.values():
                f()
        times: dict[str, list[float]] = {k: [] for k in clients}
        for _ in range(n):
            for name, f in clients.items():
                t0 = time.perf_counter()
                f()
                times[name].append((time.perf_counter() - t0) * 1000)
        requests = stub.requests
    stats = {
        k: {
            "n": len(v),
            "median_ms": round(statistics.median(v), 3),
            "p95_ms": round(percentile(v, 95), 3),
            "mean_ms": round(statistics.fmean(v), 3),
            "min_ms": round(min(v), 3),
        }
        for k, v in times.items()
    }
    return {"stats": stats, "stub_requests": requests, "n": n, "warmup": warmup}


# --------------------------------------------------------------------------- import time

IMPORTS = {
    "httpx": "import httpx",
    "ollama": "import ollama",
    "langchain_core (chat model base)": "import langchain_core.language_models.chat_models",
    "langchain_ollama": "from langchain_ollama import ChatOllama",
    "langchain (create_agent)": "from langchain.agents import create_agent",
}


def import_times(repeats: int) -> dict[str, Any]:
    out = {}
    for name, stmt in IMPORTS.items():
        code = f"import time; t = time.perf_counter(); {stmt}; print(time.perf_counter() - t)"
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)  # warm the .pyc cache
        vals = []
        for _ in range(repeats):
            r = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
            vals.append(float(r.stdout.strip()) * 1000)
        out[name] = {"median_ms": round(statistics.median(vals), 1), "min_ms": round(min(vals), 1), "runs_ms": vals}
    return out


def importtime_breakdown(stmt: str, top: int = 15) -> list[dict[str, Any]]:
    """Top-level packages by cumulative import time, from ``python -X importtime``."""
    r = subprocess.run([sys.executable, "-X", "importtime", "-c", stmt], check=True, capture_output=True, text=True)
    per_top: dict[str, int] = {}
    for line in r.stderr.splitlines():
        parts = line.removeprefix("import time:").split("|")
        if not line.startswith("import time:") or len(parts) != 3:
            continue
        try:
            self_us = int(parts[0])
        except ValueError:  # the header line
            continue
        root = parts[2].strip().split(".")[0]
        per_top[root] = per_top.get(root, 0) + self_us
    ranked = sorted(per_top.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [{"package": k, "self_ms_total": round(v / 1000, 1)} for k, v in ranked]


# --------------------------------------------------------------------------- footprint


def closure(root: str) -> set[str]:
    """The installed distributions ``root`` needs (no extras), itself included."""
    seen: set[str] = set()
    todo = [canonicalize_name(root)]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen.add(name)
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            todo.append(canonicalize_name(req.name))
    return seen


def dist_size(name: str) -> int:
    dist = metadata.distribution(name)
    total = 0
    for f in dist.files or []:
        p = f.locate()
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def footprint() -> dict[str, Any]:
    out = {}
    for root in ["httpx", "ollama", "langchain-ollama", "langchain"]:
        names = sorted(closure(root))
        size = sum(dist_size(n) for n in names)
        out[root] = {"packages": len(names), "size_mb": round(size / 1e6, 1), "names": names}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=500)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--import-repeats", type=int, default=10)
    ap.add_argument("--out")
    args = ap.parse_args()
    refuse_if_tracing()
    out = run_dir("overhead", args.out)

    calls = time_calls(args.n, args.warmup)
    print(f"Per call against the stub (N={args.n}, after {args.warmup} warm-up rounds), milliseconds:")
    print(f"  {'client':24} {'median':>8} {'p95':>8} {'mean':>8} {'min':>8}")
    for k, s in calls["stats"].items():
        print(f"  {k:24} {s['median_ms']:8.3f} {s['p95_ms']:8.3f} {s['mean_ms']:8.3f} {s['min_ms']:8.3f}")

    imports = import_times(args.import_repeats)
    print(f"\nImport time, fresh interpreter, median of {args.import_repeats} (ms):")
    for k, v in imports.items():
        print(f"  {k:34} {v['median_ms']:8.1f}")
    breakdown = importtime_breakdown("from langchain_ollama import ChatOllama")
    print("\n-X importtime for `from langchain_ollama import ChatOllama`, self time summed per top-level package (ms):")
    for row in breakdown[:10]:
        print(f"  {row['package']:24} {row['self_ms_total']:8.1f}")

    fp = footprint()
    print("\nInstall footprint (dependency closure, installed files):")
    for k, v in fp.items():
        print(f"  {k:18} {v['packages']:3} packages  {v['size_mb']:6.1f} MB")

    write_json(
        out / "results.json",
        {"environment": environment(), "calls": calls, "imports": imports, "importtime": breakdown, "footprint": fp},
    )
    print(f"\nSaved {out / 'results.json'}")


if __name__ == "__main__":
    main()
