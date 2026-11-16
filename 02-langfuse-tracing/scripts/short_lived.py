"""Part 3b: a short-lived script that exits right after its work. Which of its traces arrive?

Each mode is a fresh Python process that sends --traces requests through the traced app (fake model) and then
ends at once, in one of these ways:

  flush       calls langfuse.flush(), then returns normally
  no-flush    returns normally without flush() or shutdown() (Python still runs atexit handlers)
  exception   raises an uncaught exception without flush() (atexit handlers still run)
  sigterm     is killed by SIGTERM with the default handler, as a container stop or `kill` would do
  os-exit     calls os._exit(0), which skips atexit handlers (as a forked worker or a crash might)

The script then counts, through the Langfuse API, the traces that arrived for each mode, and how long each
process took from its last line of work to exit.

Example (about a minute, no model):
    python -m scripts.short_lived --traces 5
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from langfuse import propagate_attributes

from fleet.app import answer_traced
from fleet.lf import list_observations, make_client, make_reader
from fleet.llm import FakeChat
from scripts.common import load_questions, new_run_id, run_metadata, write_json

MODES = ("flush", "no-flush", "exception", "sigterm", "os-exit")


def child(mode: str, session: str, n: int) -> None:
    langfuse = make_client()
    questions = [q["question"] for q in load_questions()]
    with propagate_attributes(session_id=session):
        for i in range(n):
            answer_traced(questions[i % len(questions)], FakeChat(), langfuse)
    print(json.dumps({"work_done_at": time.time()}), flush=True)
    if mode == "flush":
        langfuse.flush()
        return
    if mode == "no-flush":
        return
    if mode == "exception":
        raise RuntimeError("deliberate crash after the work")
    if mode == "sigterm":
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
    if mode == "os-exit":
        os._exit(0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", type=int, default=5, help="traces per process (default 5)")
    ap.add_argument("--modes", default=",".join(MODES), help="comma-separated subset of: " + ", ".join(MODES))
    ap.add_argument("--wait", type=float, default=45, help="seconds to wait for traces to arrive (default 45)")
    ap.add_argument("--child", choices=MODES, help=argparse.SUPPRESS)
    ap.add_argument("--session", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(args.child, args.session, args.traces)
        return 0

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    run_id = new_run_id("short-lived")
    results = {}
    for mode in modes:
        session = f"{run_id}-{mode}"
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.short_lived",
                "--child",
                mode,
                "--session",
                session,
                "--traces",
                str(args.traces),
            ],
            capture_output=True,
            text=True,
        )
        ended = time.time()
        work_done = json.loads(p.stdout.strip().splitlines()[0])["work_done_at"] if p.stdout.strip() else None
        results[mode] = {
            "session": session,
            "returncode": p.returncode,
            "exit_seconds_after_work": None if work_done is None else ended - work_done,
            "stderr_tail": p.stderr.strip().splitlines()[-3:],
        }

    reader = make_reader()
    deadline = time.monotonic() + args.wait
    while True:
        for r in results.values():
            roots = list_observations(reader, fields="core", session_id=r["session"], is_root_observation=True)
            r["traces_arrived"] = len(roots)
        if all(r["traces_arrived"] >= args.traces for r in results.values()) or time.monotonic() > deadline:
            break
        time.sleep(2)

    print(f"{'mode':10} {'exit code':>9} {'exit after work':>16} {'traces arrived':>15}")
    for mode, r in results.items():
        print(
            f"{mode:10} {r['returncode']:>9} {r['exit_seconds_after_work'] or 0:>15.3f}s "
            f"{r['traces_arrived']:>9}/{args.traces}"
        )
    out = write_json(f"{run_id}.json", {"meta": run_metadata(run_id=run_id, traces=args.traces), "results": results})
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
