"""Part 1: what a trace contains, read back through the Langfuse API and compared with what the app saw.

Sends N questions through the traced app (each one is a trace: a root span, a retriever observation and a
generation), plus one more request whose generation is recorded WITHOUT usage_details. Then flushes, reads
every observation back with GET /api/public/v2/observations, and checks:

- the tree: names, types, and parent links;
- the generation: model name, parameters, and token usage against Ollama's prompt_eval_count / eval_count;
- the latency Langfuse records against the time measured in the app and Ollama's total_duration.

Example (3 + 1 model calls, under a minute with a warm model):
    python -m scripts.trace_contents --model qwen2.5:3b --n 3
"""

import argparse
import sys

from langfuse import propagate_attributes

from fleet.app import answer_traced
from fleet.config import DEFAULT_MODEL
from fleet.lf import list_observations, make_client, make_reader, wait_for
from fleet.llm import OllamaChat, make_model
from scripts.common import Timer, load_questions, ms, new_run_id, run_metadata, write_json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag, or 'fake' (default: %(default)s)")
    ap.add_argument("--n", type=int, default=3, help="questions to send with usage recorded (default: %(default)s)")
    ap.add_argument("--timeout", type=float, default=120, help="seconds to wait for the data to be readable")
    args = ap.parse_args()

    run_id = new_run_id("trace-contents")
    model = make_model(args.model)
    meta = run_metadata(run_id=run_id, model=args.model, n=args.n)
    if isinstance(model, OllamaChat):
        meta.update(model_digest=model.digest(), ollama_version=model.server_version(), options=model.parameters)

    langfuse = make_client()
    questions = load_questions()[: args.n]
    plan = [(q, True) for q in questions] + [(questions[0], False)]  # last one: no usage_details

    sent = []
    with propagate_attributes(session_id=run_id, tags=["trace-contents"]):
        for q, record_usage in plan:
            with Timer() as t:
                a = answer_traced(q["question"], model, langfuse, record_usage=record_usage)
            sent.append(
                {
                    "question_id": q["id"],
                    "trace_id": a.trace_id,
                    "record_usage": record_usage,
                    "answer": a.text,
                    "app_seconds": t.seconds,
                    "ollama_prompt_eval_count": a.input_tokens,
                    "ollama_eval_count": a.output_tokens,
                    "ollama_total_duration_s": (a.model_raw.get("total_duration") or 0) / 1e9 or None,
                }
            )
            print(f"sent {q['id']} trace={a.trace_id} usage={'yes' if record_usage else 'NO'} ({t.seconds:.2f}s)")

    with Timer() as tf:
        langfuse.flush()
    print(f"flush() took {ms(tf.seconds)} ms")

    reader = make_reader()
    expected = 3 * len(plan)
    observations, waited = wait_for(
        lambda: list_observations(reader, session_id=run_id), expected, timeout=args.timeout
    )
    print(f"read back {len(observations)}/{expected} observations after {waited:.1f}s of polling")

    by_trace: dict[str, list] = {}
    for o in observations:
        by_trace.setdefault(o.trace_id, []).append(o)

    results = []
    for s in sent:
        obs = by_trace.get(s["trace_id"], [])
        ids = {o.id: o for o in obs}
        tree = [
            {
                "name": o.name,
                "type": o.type,
                "parent": ids[o.parent_observation_id].name if o.parent_observation_id in ids else None,
                "is_root": o.is_root_observation,
                "latency_s": o.latency,
            }
            for o in sorted(obs, key=lambda o: o.start_time)
        ]
        gen = next((o for o in obs if o.type == "GENERATION"), None)
        root = next((o for o in obs if o.name == "fleet-question"), None)
        row = {**s, "tree": tree}
        if gen:
            usage = gen.usage_details or {}
            row.update(
                langfuse_model=gen.model,
                langfuse_model_parameters=gen.model_parameters,
                langfuse_usage_details=usage,
                langfuse_generation_latency_s=gen.latency,
                langfuse_total_cost=gen.total_cost,
                input_tokens_match=usage.get("input") == s["ollama_prompt_eval_count"],
                output_tokens_match=usage.get("output") == s["ollama_eval_count"],
                generation_input_is_messages=isinstance(gen.input, list | str) and "system" in str(gen.input),
            )
        if root:
            row.update(langfuse_root_latency_s=root.latency, root_input=root.input, root_output=root.output)
        results.append(row)

    print()
    print(
        f"{'q':4} {'usage sent':10} {'ollama in/out':14} {'langfuse usageDetails':40} {'match':6} "
        f"{'app s':7} {'root s':7} {'gen s':7} {'ollama s':8}"
    )
    for r in results:
        print(
            f"{r['question_id']:4} {str(r['record_usage']):10} "
            f"{str(r['ollama_prompt_eval_count']) + '/' + str(r['ollama_eval_count']):14} "
            f"{str(r.get('langfuse_usage_details')):40} "
            f"{str(r.get('input_tokens_match') and r.get('output_tokens_match')):6} "
            f"{r['app_seconds']:<7.3f} {r.get('langfuse_root_latency_s') or 0:<7.3f} "
            f"{r.get('langfuse_generation_latency_s') or 0:<7.3f} {r['ollama_total_duration_s'] or 0:<8.3f}"
        )
    if results:
        print("\ntree of the first trace:")
        for node in results[0]["tree"]:
            print(f"  {node['type']:10} {node['name']:16} parent={node['parent']} is_root={node['is_root']}")

    out = write_json(
        f"{run_id}.json",
        {
            "meta": meta,
            "flush_seconds": tf.seconds,
            "poll_seconds": waited,
            "observations_expected": expected,
            "observations_read": len(observations),
            "results": results,
            "raw_observations": [o.dict() for o in observations],
        },
    )
    print(f"\nwrote {out}")
    return 0 if len(observations) == expected else 1


if __name__ == "__main__":
    sys.exit(main())
