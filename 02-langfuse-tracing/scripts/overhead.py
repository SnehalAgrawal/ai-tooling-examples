"""Part 2: how much time tracing adds to a request.

Each condition runs in its own Python process (the SDK keeps one client per public key per process), and the
conditions are interleaved over several rounds so that drift on the machine affects them alike:

  none           the app with no Langfuse code at all (answer_plain)
  disabled       the traced app with Langfuse(tracing_enabled=False)
  null-exporter  the traced app with tracing on, but spans handed to an exporter that discards them
                 (isolates span creation, serialization and queueing from the network)
  enabled        the traced app sending to the local Langfuse server

With --model fake the model answers instantly, so the numbers isolate the SDK. Requests run back to back on
one thread; each is timed with perf_counter around the whole request. flush() at the end is timed separately
and is not part of any request. For 'enabled', the script then counts the traces that arrived.

Examples:
    python -m scripts.overhead --model fake --n 500 --rounds 3          # ~1 minute, no model
    python -m scripts.overhead --model qwen2.5:3b --n 20 --rounds 2 --warmup 2 --conditions none,enabled
"""

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Sequence

from langfuse import propagate_attributes
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from fleet.app import answer_plain, answer_traced
from fleet.config import DEFAULT_MODEL
from fleet.lf import list_observations, make_client, make_reader, queue_length, wait_for
from fleet.llm import OllamaChat, make_model
from fleet.stats import summarize
from scripts.common import load_questions, new_run_id, run_metadata, write_json

CONDITIONS = ("none", "disabled", "null-exporter", "enabled")


class NullExporter(SpanExporter):
    def __init__(self):
        self.spans = 0

    def export(self, spans: Sequence) -> SpanExportResult:
        self.spans += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def child(args) -> None:
    model = make_model(args.model)
    questions = [q["question"] for q in load_questions()]
    session = f"{args.run_id}-{args.child}-r{args.round}"
    langfuse = None
    null = None
    if args.child == "disabled":
        langfuse = make_client(tracing_enabled=False)
    elif args.child == "null-exporter":
        null = NullExporter()
        langfuse = make_client(span_exporter=null)
    elif args.child == "enabled":
        langfuse = make_client()

    def one(i: int) -> float:
        q = questions[i % len(questions)]
        start = time.perf_counter()
        if langfuse is None:
            answer_plain(q, model)
        else:
            answer_traced(q, model, langfuse)
        return time.perf_counter() - start

    timings, max_queue = [], 0
    ctx = propagate_attributes(session_id=session) if langfuse is not None else None
    if ctx:
        ctx.__enter__()
    for i in range(args.warmup):
        one(i)
    for i in range(args.n):
        timings.append(one(i))
        if args.child in ("null-exporter", "enabled"):
            max_queue = max(max_queue, queue_length(langfuse) or 0)
    if ctx:
        ctx.__exit__(None, None, None)

    flush_s = None
    if langfuse is not None:
        start = time.perf_counter()
        langfuse.flush()
        flush_s = time.perf_counter() - start

    arrived = None
    if args.child == "enabled" and args.verify:
        reader = make_reader()
        expected = args.n + args.warmup
        roots, _ = wait_for(
            lambda: list_observations(reader, fields="core,basic", session_id=session, is_root_observation=True),
            expected,
            timeout=args.verify_timeout,
        )
        arrived = len(roots)

    print(
        json.dumps(
            {
                "condition": args.child,
                "round": args.round,
                "timings_s": timings,
                "flush_s": flush_s,
                "max_queue_length": max_queue,
                "null_exporter_spans": null.spans if null else None,
                "traces_expected": args.n + args.warmup if args.child == "enabled" else None,
                "traces_arrived": arrived,
            }
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="fake", help=f"'fake' (default) or an Ollama tag such as {DEFAULT_MODEL}")
    ap.add_argument("--n", type=int, default=500, help="timed requests per condition per round (default 500)")
    ap.add_argument("--warmup", type=int, default=50, help="untimed requests first (default 50)")
    ap.add_argument("--rounds", type=int, default=3, help="interleaved rounds (default 3)")
    ap.add_argument(
        "--conditions", default=",".join(CONDITIONS), help="comma-separated subset of: " + ", ".join(CONDITIONS)
    )
    ap.add_argument("--no-verify", dest="verify", action="store_false", help="skip counting arrived traces")
    ap.add_argument("--verify-timeout", type=float, default=120)
    ap.add_argument("--child", choices=CONDITIONS, help=argparse.SUPPRESS)
    ap.add_argument("--round", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--run-id", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        child(args)
        return 0

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if c not in CONDITIONS:
            ap.error(f"unknown condition {c}")
    run_id = new_run_id("overhead")
    meta = run_metadata(run_id=run_id, model=args.model, n=args.n, warmup=args.warmup, rounds=args.rounds)
    if args.model != "fake":
        m = OllamaChat(args.model)
        meta.update(model_digest=m.digest(), ollama_version=m.server_version(), options=m.parameters)

    runs = []
    for r in range(args.rounds):
        order = conditions[r % len(conditions) :] + conditions[: r % len(conditions)]  # rotate the order each round
        for c in order:
            cmd = [
                sys.executable,
                "-m",
                "scripts.overhead",
                "--child",
                c,
                "--round",
                str(r),
                "--run-id",
                run_id,
                "--model",
                args.model,
                "--n",
                str(args.n),
                "--warmup",
                str(args.warmup),
                "--verify-timeout",
                str(args.verify_timeout),
            ]
            if not args.verify:
                cmd.append("--no-verify")
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                print(p.stderr, file=sys.stderr)
                raise SystemExit(f"child {c} failed")
            res = json.loads(p.stdout.strip().splitlines()[-1])
            res["stderr_tail"] = p.stderr.strip().splitlines()[-5:]
            s = summarize(res["timings_s"])
            arrived = (
                f" traces {res['traces_arrived']}/{res['traces_expected']}" if res["traces_arrived"] is not None else ""
            )
            print(
                f"round {r} {c:14} median {s['median'] * 1e6:9.1f} us  p95 {s['p95'] * 1e6:9.1f} us  "
                f"flush {(res['flush_s'] or 0) * 1e3:7.1f} ms  max queue {res['max_queue_length']}{arrived}"
            )
            runs.append(res)

    summary = {}
    for c in conditions:
        pooled = [t for res in runs if res["condition"] == c for t in res["timings_s"]]
        s = summarize(pooled)
        summary[c] = {
            **{k: (v * 1e6 if k != "n" else v) for k, v in s.items()},
            "per_round_median_us": [
                summarize(res["timings_s"])["median"] * 1e6 for res in runs if res["condition"] == c
            ],
            "flush_ms": [
                None if res["flush_s"] is None else res["flush_s"] * 1e3 for res in runs if res["condition"] == c
            ],
            "traces_arrived": [res["traces_arrived"] for res in runs if res["condition"] == c],
        }

    print(f"\npooled over {args.rounds} rounds x {args.n} requests (microseconds per request):")
    print(f"{'condition':14} {'median':>10} {'p95':>10} {'p99':>10} {'max':>10}   per-round medians")
    for c, s in summary.items():
        rounds = ", ".join(f"{m:.1f}" for m in s["per_round_median_us"])
        print(f"{c:14} {s['median']:10.1f} {s['p95']:10.1f} {s['p99']:10.1f} {s['max']:10.1f}   {rounds}")
    if "none" in summary:
        base = summary["none"]
        for c, s in summary.items():
            if c != "none":
                print(
                    f"{c:14} adds {s['median'] - base['median']:+.1f} us at the median, "
                    f"{s['p95'] - base['p95']:+.1f} us at p95"
                )

    out = write_json(
        f"{run_id}-{args.model.replace(':', '_')}.json", {"meta": meta, "summary_us": summary, "runs": runs}
    )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
