"""Which settings change a model's reply, and which don't: the same eight requests under different conditions.

Every condition is compared with a reference run (temperature 0, seed 42, num_ctx 8192, model loaded fresh for
each request), reply by reply: identical text, or the word where it first differs.

    python scripts/determinism.py                       # both models, about 25 minutes
    python scripts/determinism.py --models qwen2.5:3b --only reference,cold_again

Runs on dedicated Ollama servers (ports 11435 and 11436) that this script starts and stops; your usual server on
11434 is not used. Results: runs/determinism-<model>.jsonl (every reply) and runs/determinism-summary.json.
"""

import argparse
import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compare import summarise  # noqa: E402
from ollama_client import dedicated_server  # noqa: E402
from prompts import REQUESTS, messages  # noqa: E402

RUNS = Path(__file__).resolve().parents[1] / "runs"
GREEDY = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 256}
SAMPLED = {"temperature": 0.8, "seed": 42, "num_ctx": 8192, "num_predict": 256}
NO_SEED = {"temperature": 0.8, "num_ctx": 8192, "num_predict": 256}


def record(model, condition, key, response, **extra):
    keep = ("prompt_eval_count", "prompt_eval_cached_count", "prompt_eval_duration", "eval_count", "eval_duration", "load_duration",
            "total_duration", "done_reason", "wall_seconds", "usage", "system_fingerprint")
    return {"model": model, "condition": condition, "id": key, "text": response["text"],
            **{k: response[k] for k in keep if k in response}, **extra}


def one_by_one(client, model, condition, options, cold=False, twice=False, openai=False):
    """Send the eight requests in order. cold: unload the model before each. twice: send each one twice in a row."""
    rows = []
    for key, prompt in REQUESTS:
        for attempt in (1, 2) if twice else (1,):
            if cold:
                client.unload(model)
            if openai:
                response = client.chat_openai(model, messages(prompt), temperature=options["temperature"],
                                              seed=options["seed"], max_tokens=options["num_predict"])
            else:
                response = client.chat(model, messages(prompt), options)
            rows.append(record(model, condition, key, response, attempt=attempt))
    return rows


def concurrently(client, model, condition, options, width=4):
    """Send the requests in groups of `width` at the same moment."""
    rows = []
    for start in range(0, len(REQUESTS), width):
        group = REQUESTS[start:start + width]
        results = [None] * len(group)

        def send(i, prompt, results=results):
            results[i] = client.chat(model, messages(prompt), options)

        threads = [threading.Thread(target=send, args=(i, p)) for i, (_, p) in enumerate(group)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows += [record(model, condition, key, r) for (key, _), r in zip(group, results)]
    return rows


def conditions(model):
    """(name, server, function) in the order they run. server: 'default' (11435) or 'parallel4' (11436)."""
    return [
        ("reference", "default", lambda c: one_by_one(c, model, "reference", GREEDY, cold=True)),
        ("cold_again", "default", lambda c: one_by_one(c, model, "cold_again", GREEDY, cold=True)),
        ("warm_in_order", "default", lambda c: one_by_one(c, model, "warm_in_order", GREEDY)),
        ("warm_twice", "default", lambda c: one_by_one(c, model, "warm_twice", GREEDY, twice=True)),
        ("num_ctx_4096", "default", lambda c: one_by_one(c, model, "num_ctx_4096", {**GREEDY, "num_ctx": 4096})),
        ("num_ctx_16384", "default", lambda c: one_by_one(c, model, "num_ctx_16384", {**GREEDY, "num_ctx": 16384})),
        ("num_batch_64", "default", lambda c: one_by_one(c, model, "num_batch_64", {**GREEDY, "num_batch": 64})),
        ("cpu_only", "default", lambda c: one_by_one(c, model, "cpu_only", {**GREEDY, "num_gpu": 0})),
        ("openai_endpoint", "default", lambda c: one_by_one(c, model, "openai_endpoint", GREEDY, openai=True)),
        ("sampled_seed42", "default", lambda c: one_by_one(c, model, "sampled_seed42", SAMPLED, cold=True)),
        ("sampled_seed42_again", "default", lambda c: one_by_one(c, model, "sampled_seed42_again", SAMPLED, cold=True)),
        ("sampled_seed7", "default", lambda c: one_by_one(c, model, "sampled_seed7", {**SAMPLED, "seed": 7}, cold=True)),
        ("sampled_no_seed", "default", lambda c: one_by_one(c, model, "sampled_no_seed", NO_SEED, cold=True)),
        ("sampled_no_seed_again", "default", lambda c: one_by_one(c, model, "sampled_no_seed_again", NO_SEED, cold=True)),
        ("parallel4_one_by_one", "parallel4", lambda c: one_by_one(c, model, "parallel4_one_by_one", GREEDY, cold=True)),
        ("parallel4_concurrent", "parallel4", lambda c: concurrently(c, model, "parallel4_concurrent", GREEDY)),
    ]


# Which run each condition is compared with.
COMPARED_WITH = {"sampled_seed42_again": "sampled_seed42", "sampled_seed7": "sampled_seed42",
                 "sampled_no_seed_again": "sampled_no_seed"}


def compare_all(rows):
    by_condition = {}
    for row in rows:
        by_condition.setdefault((row["condition"], row.get("attempt", 1)), {})[row["id"]] = row["text"]
    summary = {}
    for (condition, attempt), replies in by_condition.items():
        base = COMPARED_WITH.get(condition, "reference")
        if (condition, attempt) == ("reference", 1) or condition in ("sampled_seed42", "sampled_no_seed"):
            continue
        if (base, 1) not in by_condition:
            continue
        label = condition if attempt == 1 else f"{condition} (second send)"
        summary[label] = {"compared_with": base, **summarise(by_condition[(base, 1)], replies)}
    if ("sampled_no_seed", 1) in by_condition:
        summary["sampled_no_seed vs reference"] = {"compared_with": "reference",
                                                   **summarise(by_condition[("reference", 1)],
                                                               by_condition[("sampled_no_seed", 1)])}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="qwen2.5:3b,llama3.2:3b")
    parser.add_argument("--only", help="comma-separated condition names (reference always runs)")
    parser.add_argument("--tag", default="", help="suffix for the output files, e.g. run2")
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    all_summaries = {"started_utc": started, "models": {}}
    for model in args.models.split(","):
        rows = []
        selected = conditions(model)
        if args.only:
            wanted = set(args.only.split(",")) | {"reference"}
            selected = [c for c in selected if c[0] in wanted]
        for server_name in ("default", "parallel4"):
            todo = [c for c in selected if c[1] == server_name]
            if not todo:
                continue
            port, settings = (11435, {}) if server_name == "default" else (11436, {"OLLAMA_NUM_PARALLEL": "4"})
            log = RUNS / f"server-{server_name}-{model.replace(':', '_')}.log"
            with dedicated_server(port, log_path=log, **settings) as client:
                for name, _, run in todo:
                    t0 = time.perf_counter()
                    new = run(client)
                    rows += new
                    loaded = [{"ctx": m.get("context_length"), "gpu": f"{m.get('size_vram', 0) / m['size']:.0%}"}
                              for m in client.ps() if m["name"] == model]
                    print(f"{model:<12} {name:<24} {len(new):>2} replies  {time.perf_counter() - t0:6.1f} s  "
                          f"loaded={loaded}", flush=True)
                client.unload(model)
        suffix = f"-{args.tag}" if args.tag else ""
        path = RUNS / f"determinism-{model.replace(':', '_')}{suffix}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        all_summaries["models"][model] = compare_all(rows)
        print(f"\n{model}: identical to the run it is compared with")
        for label, s in all_summaries["models"][model].items():
            where = ", ".join(f"{k}@word {v['word']}" for k, v in s["differences"].items())
            print(f"  {label:<38} {s['identical']}/{s['total']}  vs {s['compared_with']:<16} {where}")
    suffix = f"-{args.tag}" if args.tag else ""
    (RUNS / f"determinism-summary{suffix}.json").write_text(json.dumps(all_summaries, indent=2))


if __name__ == "__main__":
    main()
