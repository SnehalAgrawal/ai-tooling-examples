"""Part 5: masking. Does an email address or phone number in a request reach the stored trace?

One question containing an email address and a phone number goes through the traced app (fake model) three
times, each in a fresh process with a different client configuration:

  none             no masking
  mask             Langfuse(mask=...): the legacy hook, applied when the SDK sets input/output/metadata
  mask_otel_spans  Langfuse(mask_otel_spans=...): the hook the docs recommend for Python, applied to the span
                   attributes of each export batch

The email address is also set as trace-level metadata with propagate_attributes(). Then every stored observation
is read back through the API (input, output, metadata) and searched for the raw email address and phone number.

Example (a few seconds, no model):
    python -m scripts.masking
"""

import argparse
import json
import subprocess
import sys

from langfuse import propagate_attributes

from fleet.app import answer_traced
from fleet.lf import list_observations, make_client, make_reader, wait_for
from fleet.llm import FakeChat
from fleet.masking import mask_data, mask_otel_spans
from scripts.common import new_run_id, run_metadata, write_json

EMAIL = "jane.doe@example.com"
PHONE = "+44 20 7946 0958"
QUESTION = f"Driver Jane ({EMAIL}, {PHONE}) lost her fuel card. What should she do?"
HOOKS = ("none", "mask", "mask_otel_spans")


def child(hook: str, session: str) -> None:
    kwargs = {"mask": mask_data} if hook == "mask" else {"mask_otel_spans": mask_otel_spans} if hook != "none" else {}
    langfuse = make_client(**kwargs)
    with propagate_attributes(session_id=session, metadata={"reported_by": EMAIL}):
        a = answer_traced(QUESTION, FakeChat(), langfuse)
    langfuse.flush()
    print(json.dumps({"trace_id": a.trace_id, "answer_seen_by_app": a.text}))


def scan(observation) -> list[str]:
    """Fields of a stored observation that still contain the raw email or phone number."""
    hits = []
    for field in ("input", "output", "metadata"):
        text = json.dumps(getattr(observation, field), default=str)
        for label, raw in (("email", EMAIL), ("phone", PHONE)):
            if raw in text:
                hits.append(f"{field}:{label}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--child", choices=HOOKS, help=argparse.SUPPRESS)
    ap.add_argument("--session", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(args.child, args.session)
        return 0

    run_id = new_run_id("masking")
    reader = make_reader()
    results = {}
    for hook in HOOKS:
        session = f"{run_id}-{hook}"
        p = subprocess.run(
            [sys.executable, "-m", "scripts.masking", "--child", hook, "--session", session],
            capture_output=True,
            text=True,
            check=True,
        )
        sent = json.loads(p.stdout.strip().splitlines()[-1])
        obs, waited = wait_for(lambda s=session: list_observations(reader, session_id=s), 3, timeout=args.timeout)
        stored = [
            {
                "name": o.name,
                "type": o.type,
                "raw_pii_in": scan(o),
                "input": o.input,
                "output": o.output,
                "metadata": o.metadata,
            }
            for o in sorted(obs, key=lambda o: o.start_time)
        ]
        leaks = [f"{s['name']}.{h}" for s in stored for h in s["raw_pii_in"]]
        results[hook] = {
            "sent": sent,
            "observations": len(obs),
            "leaks": leaks,
            "stored": stored,
            "stderr": p.stderr.strip().splitlines()[-5:],
        }
        print(f"{hook:16} observations {len(obs)}  raw email/phone found in: {', '.join(leaks) or 'nowhere'}")

    root = next((s for s in results["mask_otel_spans"]["stored"] if s["name"] == "fleet-question"), None)
    if root:
        print(f"\nstored root input with mask_otel_spans: {root['input']}")
        print(f"stored root metadata with mask_otel_spans: {root['metadata']}")
    root = next((s for s in results["mask"]["stored"] if s["name"] == "fleet-question"), None)
    if root:
        print(f"stored root metadata with mask: {root['metadata']}")
    out = write_json(f"{run_id}.json", {"meta": run_metadata(run_id=run_id, question=QUESTION), "results": results})
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
