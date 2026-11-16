"""Part 3: what happens to the app, and to its traces, when Langfuse is down or slow.

Each scenario runs the traced app (fake model, so request time is all app + SDK) in a fresh Python process,
sending requests at a fixed rate for a fixed time, and records:

- request latency (median, p95, max) and whether any request raised;
- the SDK's in-memory span queue over time (its length, sampled after every request, and its capacity);
- spans created, spans handed to the exporter, export calls that succeeded or failed, and spans dropped
  because the queue was full (counted by wrapping the exporter; the SDK itself only logs these);
- every log record from the langfuse and opentelemetry loggers;
- how long flush() blocks, and how long the process takes to exit after its last line of code;
- how many traces actually arrived (read back from Langfuse, or counted by the fake server).

Scenarios:
  up           the local Langfuse server, healthy (control)
  closed-port  base_url points at a port where nothing listens (connection refused)
  slow         base_url points at a local HTTP server that waits --slow-seconds before answering 200;
               it decodes and counts the spans it receives
  error-503    a local HTTP server that answers every export with 503 (a retryable status)
  stopped      the real server: `docker compose stop langfuse-web` after a third of the requests, `start`
               after two thirds (the web container then needs some seconds to become healthy again);
               reports which requests' traces never arrived (needs Docker; takes ~1-2 minutes)

Example:
    python -m scripts.outage --scenarios up,closed-port,slow,error-503 --rate 50 --duration 20
    python -m scripts.outage --scenarios stopped --rate 20 --duration 60
"""

import argparse
import gzip
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from langfuse import propagate_attributes
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from fleet.app import answer_traced
from fleet.config import LANGFUSE_BASE_URL
from fleet.lf import langfuse_processor, list_observations, make_client, make_reader, queue_capacity, wait_for
from fleet.llm import FakeChat
from fleet.stats import summarize
from scripts.common import DEMO_DIR, load_questions, new_run_id, run_metadata, write_json

SCENARIOS = ("up", "closed-port", "slow", "error-503", "stopped")


# ---------------------------------------------------------------- fake Langfuse endpoints (parent process)


class FakeCollector:
    """An HTTP server standing in for Langfuse's OTLP endpoint: it can be slow or fail, and counts what it gets."""

    def __init__(self, delay: float = 0.0, status: int = 200):
        self.delay, self.status = delay, status
        self.requests = 0
        self.spans = 0
        self.span_ids: set[bytes] = set()
        self.lock = threading.Lock()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                req = ExportTraceServiceRequest()
                req.ParseFromString(body)
                ids = [s.span_id for rs in req.resource_spans for ss in rs.scope_spans for s in ss.spans]
                with collector.lock:
                    collector.requests += 1
                    collector.spans += len(ids)
                    collector.span_ids.update(ids)
                time.sleep(collector.delay)
                try:
                    self.send_response(collector.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stats(self):
        with self.lock:
            return {
                "requests": self.requests,
                "spans_received": self.spans,
                "unique_spans_received": len(self.span_ids),
            }

    def close(self):
        self.server.shutdown()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(DEMO_DIR / "docker-compose.yml"), *args], check=True, capture_output=True
    )


def wait_healthy(timeout: float = 180) -> float:
    import httpx

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if httpx.get(f"{LANGFUSE_BASE_URL}/api/public/health", timeout=2).status_code == 200:
                return time.monotonic() - start
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Langfuse did not become healthy")


# ---------------------------------------------------------------- the app under test (child process)


class CountingExporter:
    """Wraps the SDK's span exporter to count what reaches it and what each export returns."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = self.ok = self.failed = 0
        self.spans_in = self.spans_ok = 0
        self.durations: list[float] = []

    def export(self, spans):
        start = time.perf_counter()
        result = self.inner.export(spans)
        self.durations.append(time.perf_counter() - start)
        self.calls += 1
        self.spans_in += len(spans)
        if result.name == "SUCCESS":
            self.ok += 1
            self.spans_ok += len(spans)
        else:
            self.failed += 1
        return result

    def shutdown(self, *a, **kw):
        return self.inner.shutdown(*a, **kw)

    def force_flush(self, *a, **kw):
        return self.inner.force_flush(*a, **kw)


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[dict] = []

    def emit(self, record):
        self.records.append(
            {
                "t": record.created,
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage()[:300],
            }
        )


def child(args) -> None:
    capture = LogCapture()
    for name in ("langfuse", "opentelemetry"):
        lg = logging.getLogger(name)
        lg.addHandler(capture)
        lg.setLevel(logging.INFO)
    # Keep the SDK's own console output too, so the parent can show what a developer would see.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    langfuse = make_client(base_url=args.base_url)
    processor = langfuse_processor(langfuse)
    bp = processor._batch_processor  # noqa: SLF001
    counter = CountingExporter(bp._exporter)  # noqa: SLF001
    bp._exporter = counter  # noqa: SLF001

    model = FakeChat()
    questions = [q["question"] for q in load_questions()]
    interval = 1.0 / args.rate
    total = int(args.rate * args.duration)
    timings, errors, queue_samples, trace_ids, offsets = [], [], [], [], []
    marks = {}
    t0 = time.monotonic()
    t0_wall = time.time()
    with propagate_attributes(session_id=args.session):
        for i in range(total):
            target = t0 + i * interval
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
            if args.signal_file and i == total // 3:
                marks["stop_requested_at_request"] = i
                print("STOP", flush=True)  # tell the parent to stop langfuse-web now
            if args.signal_file and i == 2 * total // 3:
                marks["start_requested_at_request"] = i
                print("START", flush=True)
            offsets.append(round(time.monotonic() - t0, 3))
            start = time.perf_counter()
            try:
                trace_ids.append(answer_traced(questions[i % len(questions)], model, langfuse).trace_id)
            except Exception as e:  # noqa: BLE001 - the point is to see whether anything escapes
                errors.append(repr(e))
                trace_ids.append(None)
            timings.append(time.perf_counter() - start)
            queue_samples.append((round(time.monotonic() - t0, 3), len(bp._queue)))  # noqa: SLF001
    phase_s = time.monotonic() - t0

    if args.signal_file:
        # wait until the parent says the server is back, so flush() has somewhere to send
        while not open(args.signal_file).read().strip():
            time.sleep(0.5)

    queue_before_flush = len(bp._queue)  # noqa: SLF001
    start = time.perf_counter()
    langfuse.flush()
    flush_s = time.perf_counter() - start

    spans_created = 3 * total
    result = {
        "requests": total,
        "request_errors": len(errors),
        "first_errors": errors[:3],
        "latency": summarize(timings),
        "phase_seconds": phase_s,
        "queue_capacity": queue_capacity(langfuse),
        "queue_max": max(q for _, q in queue_samples),
        "queue_samples": queue_samples[:: max(1, len(queue_samples) // 200)],
        "queue_before_flush": queue_before_flush,
        "flush_seconds": flush_s,
        "spans_created": spans_created,
        "spans_to_exporter": counter.spans_in,
        "spans_exported_ok": counter.spans_ok,
        "spans_dropped_queue_full": spans_created - counter.spans_in - len(bp._queue),  # noqa: SLF001
        "export_calls": counter.calls,
        "export_ok": counter.ok,
        "export_failed": counter.failed,
        "export_call_seconds": summarize(counter.durations) if counter.durations else None,
        "log_counts": {},
        "logs_first": capture.records[:15],
        "marks": marks,
        "t0_wall": t0_wall,
        "trace_ids": trace_ids,
        "request_offsets_s": offsets,
        "main_done_at": time.time(),
    }
    for r in capture.records:
        key = f"{r['level']} {r['logger']}: {r['message'][:90]}"
        result["log_counts"][key] = result["log_counts"].get(key, 0) + 1
    print("RESULT " + json.dumps(result), flush=True)


# ---------------------------------------------------------------- orchestration


def lost_ranges(result: dict, arrived: set[str]) -> list[dict]:
    """Contiguous runs of requests whose trace never arrived, with their send times (s after the first request)."""
    out, cur = [], None
    for i, (tid, t) in enumerate(zip(result["trace_ids"], result["request_offsets_s"])):
        if tid not in arrived:
            if cur is None:
                cur = {"first_request": i, "last_request": i, "from_s": t, "to_s": t}
            else:
                cur.update(last_request=i, to_s=t)
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def run_scenario(name: str, args, run_id: str) -> dict:
    session = f"{run_id}-{name}"
    collector = None
    signal_file = None
    if name == "up" or name == "stopped":
        base_url = LANGFUSE_BASE_URL
    elif name == "closed-port":
        base_url = f"http://127.0.0.1:{free_port()}"
    elif name == "slow":
        collector = FakeCollector(delay=args.slow_seconds)
        base_url = collector.url
    elif name == "error-503":
        collector = FakeCollector(status=503)
        base_url = collector.url
    else:
        raise ValueError(name)

    cmd = [
        sys.executable,
        "-m",
        "scripts.outage",
        "--child",
        "--base-url",
        base_url,
        "--session",
        session,
        "--rate",
        str(args.rate),
        "--duration",
        str(args.duration),
    ]
    if name == "stopped":
        signal_file = DEMO_DIR / "runs" / f".{session}.signal"
        signal_file.parent.mkdir(exist_ok=True)
        signal_file.write_text("")
        cmd += ["--signal-file", str(signal_file)]

    events = {}
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_lines: list[str] = []
    threading.Thread(target=lambda: stderr_lines.extend(p.stderr), daemon=True).start()
    result = None
    for line in p.stdout:
        line = line.strip()
        if line == "STOP":
            t = time.time()
            compose("stop", "langfuse-web")
            events["web_stop_called_at_wall"] = t
            events["web_stopped_at_wall"] = time.time()
            events["docker_stop_s"] = round(time.time() - t, 2)
        elif line == "START":
            events["web_start_called_at_wall"] = time.time()
            compose("start", "langfuse-web")
            events["healthy_after_start_s"] = round(wait_healthy(), 2)
            events["healthy_at_wall"] = time.time()
            signal_file.write_text("up")
        elif line.startswith("RESULT "):
            result = json.loads(line[len("RESULT ") :])
    if signal_file and not signal_file.read_text().strip():
        signal_file.write_text("up")
    p.wait()
    ended = time.time()
    if signal_file:
        signal_file.unlink(missing_ok=True)
    if result is None:
        raise SystemExit(f"scenario {name} failed:\n" + "".join(stderr_lines[-20:]))
    result["exit_seconds_after_main"] = ended - result.pop("main_done_at")
    result["stderr_tail"] = [ln.rstrip() for ln in stderr_lines[-8:]]
    result["stderr_lines"] = len(stderr_lines)
    result["events"] = events
    result["base_url"] = base_url

    if collector:
        time.sleep(1)
        result["collector"] = collector.stats()
        collector.close()
    if name in ("up", "stopped"):
        reader = make_reader()
        roots, waited = wait_for(
            lambda: list_observations(reader, fields="core,basic", session_id=session, is_root_observation=True),
            result["requests"],
            timeout=args.verify_timeout,
            settle=3,
        )
        result["traces_arrived"] = len(roots)
        result["traces_poll_seconds"] = waited
        result["lost_ranges"] = lost_ranges(result, {o.trace_id for o in roots})
        t0 = result["t0_wall"]
        for k in [k for k in result["events"] if k.endswith("_at_wall")]:
            result["events"][k.replace("_at_wall", "_at_s")] = round(result["events"].pop(k) - t0, 2)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--scenarios",
        default="up,closed-port,slow,error-503",
        help="comma-separated subset of: " + ", ".join(SCENARIOS) + " (default: all but 'stopped')",
    )
    ap.add_argument("--rate", type=float, default=50, help="requests per second (default 50)")
    ap.add_argument("--duration", type=float, default=20, help="seconds of requests (default 20)")
    ap.add_argument("--slow-seconds", type=float, default=10, help="delay of the 'slow' server (default 10)")
    ap.add_argument("--verify-timeout", type=float, default=90)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--base-url", help=argparse.SUPPRESS)
    ap.add_argument("--session", help=argparse.SUPPRESS)
    ap.add_argument("--signal-file", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        child(args)
        return 0

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    for s in scenarios:
        if s not in SCENARIOS:
            ap.error(f"unknown scenario {s}")
    run_id = new_run_id("outage")
    meta = run_metadata(
        run_id=run_id, rate=args.rate, duration=args.duration, slow_seconds=args.slow_seconds, model="fake"
    )
    results = {}
    for s in scenarios:
        print(f"--- {s}", flush=True)
        r = run_scenario(s, args, run_id)
        results[s] = r
        lat = r["latency"]
        arrived = r.get("traces_arrived")
        col = r.get("collector")
        print(
            f"requests {r['requests']}, errors {r['request_errors']}, latency median {lat['median'] * 1e3:.3f} ms, "
            f"p95 {lat['p95'] * 1e3:.3f} ms, max {lat['max'] * 1e3:.1f} ms"
        )
        print(
            f"queue max {r['queue_max']}/{r['queue_capacity']}, before flush {r['queue_before_flush']}; "
            f"flush {r['flush_seconds']:.2f} s; exit after main {r['exit_seconds_after_main']:.2f} s"
        )
        print(
            f"spans created {r['spans_created']}, to exporter {r['spans_to_exporter']}, exported ok "
            f"{r['spans_exported_ok']}, dropped (queue full) {r['spans_dropped_queue_full']}; export calls "
            f"{r['export_calls']} ({r['export_ok']} ok, {r['export_failed']} failed)"
        )
        if arrived is not None:
            print(
                f"traces arrived in Langfuse: {arrived}/{r['requests']}; lost: "
                + (
                    ", ".join(
                        f"requests {x['first_request']}-{x['last_request']} (sent {x['from_s']}-{x['to_s']} s)"
                        for x in r["lost_ranges"]
                    )
                    or "none"
                )
            )
        if col:
            print(
                f"fake server received {col['requests']} requests, {col['spans_received']} spans "
                f"({col['unique_spans_received']} unique)"
            )
        if r["events"]:
            print(f"events: {r['events']}")
        for k, v in list(r["log_counts"].items())[:6]:
            print(f"  log x{v}: {k}")
    out = write_json(f"{run_id}.json", {"meta": meta, "results": results})
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
