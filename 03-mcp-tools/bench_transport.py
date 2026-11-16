"""Measurement 5: what the transport costs, with no model involved (pure protocol).

    python bench_transport.py                 # N=200 for each measurement; about 5 minutes, mostly stdio startups
    python bench_transport.py --n 10          # a smoke test

Measures, for both connection modes (auto: server/discover, the SDK default; legacy: the initialize handshake):
  startup   stdio: spawn the server process + handshake, then the first tools/list, then shutdown
            http:  connect to an already-running server + handshake, then tools/list, then close
  call      per tools/call latency on one open connection (get_package_status), stdio vs Streamable HTTP
  shared    two clients calling the same Streamable HTTP server at the same time (one server, two hosts)
Baselines: starting Python with nothing (`python -c pass`) and importing the server module, to split the
stdio startup time into "start a Python process" and "MCP".

Writes runs/transport-<time>/results.json (summary and every sample) and setup.json.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from host import HERE, PYTHON
from runlib import run_dir, setup, table, write_json

MODES = ["auto", "legacy"]


def stats(samples_s: list[float]) -> dict[str, float]:
    ms = sorted(s * 1000 for s in samples_s)

    def pct(p: float) -> float:
        return ms[min(len(ms) - 1, round(p * (len(ms) - 1)))]

    return {"n": len(ms), "mean_ms": round(statistics.fmean(ms), 3), "p50_ms": round(pct(0.5), 3),
            "p90_ms": round(pct(0.9), 3), "p99_ms": round(pct(0.99), 3), "min_ms": round(ms[0], 3),
            "max_ms": round(ms[-1], 3)}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"server on port {port} did not start")


def stdio_params() -> StdioServerParameters:
    return StdioServerParameters(command=PYTHON, args=[str(HERE / "fleet_server.py")], cwd=str(HERE))


def package_ids(n: int) -> list[str]:
    return [f"PKG-{(i % 400) + 1:05d}" for i in range(n)]


async def startup(target: Any, mode: str, n: int, errlog: Any) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {"connect": [], "first_tools_list": [], "close": [], "total": []}
    for _ in range(n):
        t0 = time.perf_counter()
        client = Client(target, mode=mode) if isinstance(target, str) else Client(_stdio(target, errlog), mode=mode)
        await client.__aenter__()
        t1 = time.perf_counter()
        await client.list_tools()
        t2 = time.perf_counter()
        await client.__aexit__(None, None, None)
        t3 = time.perf_counter()
        out["connect"].append(t1 - t0)
        out["first_tools_list"].append(t2 - t1)
        out["close"].append(t3 - t2)
        out["total"].append(t3 - t0)
    return out


def _stdio(params: StdioServerParameters, errlog: Any) -> Any:
    return stdio_client(params, errlog=errlog)


async def call_latency(target: Any, mode: str, n: int, warmup: int, errlog: Any) -> list[float]:
    client = Client(target, mode=mode) if isinstance(target, str) else Client(_stdio(target, errlog), mode=mode)
    samples = []
    async with client:
        for i, pid in enumerate(package_ids(warmup + n)):
            t0 = time.perf_counter()
            result = await client.call_tool("get_package_status", {"package_id": pid})
            elapsed = time.perf_counter() - t0
            if result.is_error:
                raise RuntimeError(f"call failed: {result}")
            if i >= warmup:
                samples.append(elapsed)
    return samples


async def shared_server(url: str, mode: str, n: int) -> dict[str, Any]:
    """Two clients (two hosts) on one Streamable HTTP server, calling concurrently."""
    seen: dict[str, list[str]] = {}

    async def one(name: str, offset: int) -> None:
        async with Client(url, mode=mode) as client:
            statuses = []
            for pid in package_ids(n + offset)[offset:]:
                result = await client.call_tool("get_package_status", {"package_id": pid})
                statuses.append(result.structured_content["status"])
            seen[name] = statuses

    t0 = time.perf_counter()
    async with anyio.create_task_group() as tg:
        tg.start_soon(one, "host_a", 0)
        tg.start_soon(one, "host_b", 7)
    return {"mode": mode, "calls_per_client": n, "seconds": round(time.perf_counter() - t0, 3),
            "completed": {k: len(v) for k, v in seen.items()}}


def process_baseline(code: str, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        subprocess.run([PYTHON, "-c", code], cwd=HERE, check=True)
        samples.append(time.perf_counter() - t0)
    return samples


async def run(args: argparse.Namespace) -> int:
    out = run_dir("transport", args.out)
    write_json(out / "setup.json", setup(n=args.n, warmup=args.warmup, startup_n=args.startup_n))
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    results: dict[str, Any] = {"summary": [], "samples": {}, "shared": []}
    errlog = (out / "server-stderr.log").open("w")
    http = subprocess.Popen([PYTHON, str(HERE / "fleet_server.py"), "--transport", "http", "--port", str(port)],
                            cwd=HERE, stdout=errlog, stderr=errlog)
    try:
        wait_for_port(port)
        for name, code in (("python -c pass", "pass"), ("python -c 'import fleet_server'", "import fleet_server")):
            s = process_baseline(code, args.baseline_n)
            results["samples"][f"baseline|{name}"] = s
            results["summary"].append({"what": "baseline", "transport": name, "mode": "", **stats(s)})
            print(f"baseline {name}: {stats(s)}", flush=True)
        for mode in MODES:
            for transport, target in (("stdio", stdio_params()), ("http", url)):
                s = await startup(target, mode, args.startup_n, errlog)
                for part, values in s.items():
                    results["samples"][f"startup|{transport}|{mode}|{part}"] = values
                    results["summary"].append({"what": f"startup:{part}", "transport": transport, "mode": mode,
                                               **stats(values)})
                print(f"startup {transport} {mode}: connect {stats(s['connect'])['p50_ms']} ms p50", flush=True)
                c = await call_latency(target, mode, args.n, args.warmup, errlog)
                results["samples"][f"call|{transport}|{mode}"] = c
                results["summary"].append({"what": "tools/call", "transport": transport, "mode": mode, **stats(c)})
                print(f"tools/call {transport} {mode}: {stats(c)}", flush=True)
            results["shared"].append(await shared_server(url, mode, args.shared_n))
    finally:
        http.terminate()
        http.wait(timeout=10)
        errlog.close()
    write_json(out / "results.json", results)
    rows = [[r["what"], r["transport"], r["mode"], r["n"], r["p50_ms"], r["p90_ms"], r["p99_ms"], r["mean_ms"]]
            for r in results["summary"]]
    print("\n" + table(rows, ["what", "transport", "mode", "n", "p50 ms", "p90 ms", "p99 ms", "mean ms"]))
    for s in results["shared"]:
        print(f"one HTTP server, two clients at once ({s['mode']}): {s['completed']} calls in {s['seconds']} s")
    print(f"\n-> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=200, help="tools/call samples per transport and mode")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--startup-n", type=int, default=200, help="connect/close cycles per transport and mode")
    parser.add_argument("--baseline-n", type=int, default=50)
    parser.add_argument("--shared-n", type=int, default=50)
    parser.add_argument("--out", type=Path)
    return anyio.run(run, parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
