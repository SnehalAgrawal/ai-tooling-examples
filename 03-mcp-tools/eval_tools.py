"""Measurements 2 and 3: tool-use accuracy per model, with descriptive and with terse tool descriptions.

    python eval_tools.py                                   # 3 models x 20 questions x 2 description styles
    python eval_tools.py --descriptions descriptive        # measurement 2 only
    python eval_tools.py --models llama3.2:1b --limit 2    # a smoke test

The two description styles run the same server code with the same tools, schemas (types, enums, patterns),
system prompt, and options; only the description strings differ (see DESCRIPTIONS in fleet_server.py).
Each model is unloaded before and after its questions. Every call and grade is in results.jsonl.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import anyio

from gold import compute_gold, grade
from host import MAX_TOOL_ROUNDS, Host, fleet_server
from runlib import append_jsonl, read_jsonl, run_dir, setup, table, unload, write_json

MODELS = ["qwen2.5:3b", "llama3.2:3b", "llama3.2:1b"]


def summary_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[(r["model"], r["descriptions"])].append(r)
    rows = []
    for (model, style), rs in groups.items():
        n = len(rs)
        g = [r["grade"] for r in rs]
        rows.append([model, style, n, sum(x["tool_ok"] for x in g), sum(x["first_try_ok"] for x in g),
                     sum(x["answer_ok"] for x in g), sum(x["tool_ok"] and x["answer_ok"] for x in g),
                     sum(x["n_calls"] for x in g), sum(r["stop"] == "round_cap" for r in rs)])
    return rows


def category_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[(r["model"], r["descriptions"], r["category"])].append(r)
    return [[m, s, c, len(rs), sum(r["grade"]["tool_ok"] for r in rs), sum(r["grade"]["answer_ok"] for r in rs)]
            for (m, s, c), rs in groups.items()]


SUMMARY_HEADER = ["model", "descriptions", "n", "right tool+args", "first try", "values in answer", "both",
                  "calls", "round cap"]


async def run(args: argparse.Namespace) -> int:
    out = run_dir("tools", args.out)
    questions = compute_gold()
    if args.questions:
        questions = [q for q in questions if q.qid in args.questions.split(",")]
    questions = questions[: args.limit] if args.limit else questions
    styles = ["descriptive", "terse"] if args.descriptions == "both" else [args.descriptions]
    write_json(out / "setup.json", setup(args.models, styles=styles, questions=[q.qid for q in questions],
                                         max_rounds=args.max_rounds, repeats=args.repeats))
    results = out / "results.jsonl"
    with (out / "server-stderr.log").open("a") as errlog:
        for model in args.models:
            unload(model)
            for style in styles:
                async with Host([fleet_server("--descriptions", style)], errlog=errlog) as host:
                    for rep in range(args.repeats):
                        for q in questions:
                            r = await host.ask(q.text, model=model, max_rounds=args.max_rounds)
                            r.update(qid=q.qid, category=q.category, descriptions=style, repeat=rep, gold=q.values,
                                     grade=grade(q, r["calls"], r["answer"]))
                            append_jsonl(results, r)
                            g = r["grade"]
                            calls = ", ".join(f"{c['name']}({c['arguments']})" for c in r["calls"]) or "no call"
                            print(f"{model:12} {style:11} {q.qid} tool={'Y' if g['tool_ok'] else 'n'} "
                                  f"value={'Y' if g['answer_ok'] else 'n'}  {calls}", flush=True)
            unload(model)
    records = read_jsonl(results)
    print("\n" + table(summary_rows(records), SUMMARY_HEADER))
    print("\n" + table(category_rows(records), ["model", "descriptions", "category", "n", "right tool+args",
                                                "values in answer"]))
    print(f"\n-> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=MODELS)
    parser.add_argument("--descriptions", choices=["descriptive", "terse", "both"], default="both")
    parser.add_argument("--questions", help="comma-separated question IDs (default: all)")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=MAX_TOOL_ROUNDS)
    parser.add_argument("--out", type=Path)
    return anyio.run(run, parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
