"""Part 4: an experiment in Langfuse over a small dataset, with deterministic scores read back through the API.

1. Uploads data/questions.json as a Langfuse dataset (items are upserted by a fixed ID, so re-running is safe).
2. Runs Langfuse's run_experiment over the dataset items: each item runs the traced fleet assistant, then
   three item-level evaluators record scores on the item's trace:
     expected_facts_present_fraction  share of the item's expected facts found in the answer (0 to 1)
     all_expected_facts_present       1 if every expected fact was found, else 0
     source_retrieved                 1 if retrieval returned the snippet that holds the answer
   and one run-level evaluator records the mean of all_expected_facts_present.
   "Fact present" means one accepted spelling appears in the answer (see fleet/evaluate.py). It is not a
   judgement of correctness: an answer can contain the fact and still say something wrong.
3. Flushes, then reads the scores back with GET /api/public/v3/scores (the run-level score by experiment ID,
   item scores by trace ID), and checks that every item's scores are on the trace the SDK reported for it,
   with the values computed locally.

Examples:
    python -m scripts.evaluate --model qwen2.5:3b              # 14 model calls
    python -m scripts.evaluate --model fake --limit 3          # plumbing check, no model
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from statistics import mean

from langfuse import Evaluation

from fleet.app import answer_traced
from fleet.config import DEFAULT_MODEL
from fleet.evaluate import facts_present
from fleet.lf import make_client, make_reader, wait_for
from fleet.llm import OllamaChat, make_model
from scripts.common import Timer, load_questions, new_run_id, run_metadata, write_json

DATASET = "fleet-assistant-questions"
ITEM_SCORES = ("expected_facts_present_fraction", "all_expected_facts_present", "source_retrieved")


def upload(langfuse, questions) -> None:
    try:
        langfuse.create_dataset(
            name=DATASET,
            description="Questions about the demo delivery fleet, each with the key facts a good answer states.",
        )
    except Exception as e:  # noqa: BLE001 - an existing dataset is fine; anything else is reported below
        print(f"create_dataset: {type(e).__name__}: {str(e)[:120]}")
    for q in questions:
        langfuse.create_dataset_item(
            dataset_name=DATASET,
            id=f"fleet-assistant-{q['id']}",
            input={"question": q["question"]},
            expected_output={"expected_facts": q["expected_facts"]},
            metadata={"question_id": q["id"], "source": q["source"]},
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag, or 'fake' (default: %(default)s)")
    ap.add_argument("--limit", type=int, help="run only the first N items (by question ID)")
    ap.add_argument("--run-name", help="experiment run name (default: generated)")
    ap.add_argument("--timeout", type=float, default=120, help="seconds to wait for scores to be readable")
    args = ap.parse_args()

    run_id = new_run_id("evaluate")
    run_name = args.run_name or f"{args.model}-{run_id}"
    model = make_model(args.model)
    meta = run_metadata(run_id=run_id, model=args.model, dataset=DATASET, run_name=run_name)
    if isinstance(model, OllamaChat):
        meta.update(model_digest=model.digest(), ollama_version=model.server_version(), options=model.parameters)

    langfuse = make_client()
    questions = load_questions()
    with Timer() as tu:
        upload(langfuse, questions)
    print(f"uploaded {len(questions)} items to dataset '{DATASET}' in {tu.seconds:.2f}s")

    dataset = langfuse.get_dataset(DATASET)
    items = sorted(dataset.items, key=lambda it: (it.metadata or {}).get("question_id", ""))
    items = [it for it in items if (it.metadata or {}).get("question_id") in {q["id"] for q in questions}]
    if args.limit:
        items = items[: args.limit]

    retrieved: dict[str, list[str]] = {}

    def task(*, item, **kwargs):
        a = answer_traced(item.input["question"], model, langfuse)
        retrieved[a.trace_id] = a.sources
        return {"answer": a.text, "sources": a.sources}

    def fleet_checks(*, input, output, expected_output, metadata=None, **kwargs):
        found = facts_present(output["answer"], expected_output["expected_facts"])
        source = (metadata or {}).get("source")
        return [
            Evaluation(
                name="expected_facts_present_fraction",
                value=sum(found) / len(found),
                comment=f"{sum(found)} of {len(found)} expected facts found",
                data_type="NUMERIC",
            ),
            Evaluation(name="all_expected_facts_present", value=all(found), data_type="BOOLEAN"),
            Evaluation(
                name="source_retrieved",
                value=source is None or source in output["sources"],
                comment="no source: the knowledge base does not answer this" if source is None else None,
                data_type="BOOLEAN",
            ),
        ]

    def share_all_facts_present(*, item_results, **kwargs):
        values = [float(e.value) for r in item_results for e in r.evaluations if e.name == "all_expected_facts_present"]
        return Evaluation(name="share_all_expected_facts_present", value=mean(values) if values else 0.0)

    started = datetime.now(UTC) - timedelta(minutes=1)
    with Timer() as tr:
        result = langfuse.run_experiment(
            name="fleet-assistant",
            run_name=run_name,
            description=f"Fleet assistant answers with {args.model}; deterministic fact checks.",
            data=items,
            task=task,
            evaluators=[fleet_checks],
            run_evaluators=[share_all_facts_present],
            max_concurrency=1,
            metadata={"model": args.model},
        )
    with Timer() as tf:
        langfuse.flush()
    print(f"experiment ran {len(result.item_results)} items in {tr.seconds:.1f}s; flush {tf.seconds * 1000:.0f} ms")
    print(f"experiment_id={result.experiment_id}")

    reader = make_reader()

    def pages(**filters):
        out, cursor = [], None
        while True:
            page = reader.scores.get_many(fields="details,subject", limit=100, cursor=cursor, **filters)
            out.extend(page.data)
            cursor = getattr(page.meta, "cursor", None)
            if not cursor or not page.data:
                return out

    def fetch_scores():
        # Item scores are attached to each item's trace (subject: the task observation); filtering by
        # experiment_id returned only the run-level score on Langfuse 4.72.0, so read item scores per trace.
        run_level = pages(experiment_id=result.experiment_id)
        per_trace = [s for r in result.item_results for s in pages(trace_id=r.trace_id)]
        return run_level + per_trace

    expected = len(items) * len(ITEM_SCORES) + 1
    scores, waited = wait_for(fetch_scores, expected, timeout=args.timeout, interval=2)
    print(f"read back {len(scores)}/{expected} scores after {waited:.1f}s of polling")

    def fetch_experiment_items():
        page = reader.experiments.list_items(
            from_start_time=started, experiment_id=result.experiment_id, fields="core,dataset,scores", limit=100
        )
        return page.data

    exp_items, exp_waited = wait_for(fetch_experiment_items, len(items), timeout=args.timeout, interval=2)
    print(f"experiments API lists {len(exp_items)}/{len(items)} items for this experiment ({exp_waited:.1f}s)")

    trace_of_item = {r.item.id: r.trace_id for r in result.item_results}
    rows = []
    for r in result.item_results:
        qid = (r.item.metadata or {}).get("question_id")
        ev = {e.name: e.value for e in r.evaluations}
        stored = {
            s.name: s for s in scores if s.subject is not None and getattr(s.subject, "trace_id", None) == r.trace_id
        }
        rows.append(
            {
                "question_id": qid,
                "trace_id": r.trace_id,
                "answer": r.output["answer"] if isinstance(r.output, dict) else r.output,
                "local": ev,
                "stored": {k: v.value for k, v in stored.items()},
                "stored_matches_local": all(
                    k in stored and float(stored[k].value) == float(ev[k]) for k in ITEM_SCORES if k in ev
                ),
            }
        )

    print(f"\n{'q':4} {'facts':6} {'all':5} {'source':7} {'stored on trace':16} answer")
    for row in rows:
        loc = row["local"]
        print(
            f"{row['question_id']:4} {loc.get('expected_facts_present_fraction', 0):<6.2f} "
            f"{int(bool(loc.get('all_expected_facts_present'))):<5} {int(bool(loc.get('source_retrieved'))):<7} "
            f"{str(row['stored_matches_local']):16} {row['answer'][:70]!r}"
        )
    run_scores = [s for s in scores if s.name == "share_all_expected_facts_present"]
    for e in result.run_evaluations:
        print(f"\nrun-level {e.name} = {e.value:.3f} (stored: {[s.value for s in run_scores]})")
    linked = sum(1 for row in rows if row["stored_matches_local"])
    print(
        f"items whose {len(ITEM_SCORES)} scores were read back on their own trace with the same values: "
        f"{linked}/{len(rows)}"
    )

    out = write_json(
        f"{run_id}.json",
        {
            "meta": meta,
            "experiment_id": result.experiment_id,
            "experiment_url": result.experiment_url,
            "run_name": result.run_name,
            "run_seconds": tr.seconds,
            "scores_expected": expected,
            "scores_read": len(scores),
            "items": rows,
            "run_evaluations": [{"name": e.name, "value": e.value} for e in result.run_evaluations],
            "trace_of_item": trace_of_item,
            "raw_scores": [s.dict() for s in scores],
            "raw_experiment_items": [e.dict() for e in exp_items],
        },
    )
    print(f"\nwrote {out}")
    return 0 if len(scores) == expected and linked == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
