"""Measurement 2: structured output, per method and per model, graded field by field.

Fifteen delivery-exception messages (fleet.CASES), each extracted into the DeliveryException model with
``with_structured_output(..., include_raw=True)`` for each method, plus the same request written by
hand (``format`` = the JSON schema, httpx, Pydantic validation) as a baseline. Every request goes
through the logging proxy, so the number of requests per extraction is counted (a retry would show).

Then, against the stub (no model), each method is fed replies that should fail, with
include_raw=False and True, to record exactly what LangChain raises or returns.

    python structured.py                                   # 3 models x 4 methods x 15 = 180 requests
    python structured.py --models qwen2.5:3b --limit 2     # a smoke run: 8 requests
    python structured.py --failure-modes-only              # stub only, no model
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

import fleet
from analysis import grade, schema_valid
from common import MODELS, SAMPLING, environment, refuse_if_tracing, run_dir, stop_models, write_json
from wire import OLLAMA_URL, LoggingProxy, StubOllama

METHODS = ["json_schema", "function_calling", "json_mode", "plain_json_schema"]
FIELDS = ["package_id", "city", "reason", "delay_hours"]


def lc_messages(text: str) -> list:
    return [SystemMessage(fleet.EXTRACTION_PROMPT), HumanMessage(text)]


def err_name(e: BaseException | None) -> str | None:
    return None if e is None else f"{type(e).__module__}.{type(e).__name__}"


def raw_json(content: str) -> Any:
    """Strict JSON parse of a reply (no fence stripping): None if it is not JSON."""
    try:
        return json.loads(content)
    except ValueError:
        return None


def extract_langchain(base_url: str, model: str, method: str, text: str) -> dict[str, Any]:
    llm = ChatOllama(model=model, base_url=base_url, **SAMPLING)
    runnable = llm.with_structured_output(fleet.DeliveryException, method=method, include_raw=True)
    out = runnable.invoke(lc_messages(text))
    raw = out["raw"]
    parsed = out["parsed"]
    content = raw.content if isinstance(raw.content, str) else json.dumps(raw.content)
    if method == "function_calling":
        candidate = raw.tool_calls[0]["args"] if raw.tool_calls else None
    else:
        candidate = raw_json(content)
    valid, why = schema_valid(fleet.DeliveryException, parsed if parsed is not None else candidate)
    if parsed is not None:
        outcome = "parsed"
    elif out["parsing_error"] is not None:
        outcome = f"error:{type(out['parsing_error']).__name__}"
    else:
        outcome = "none_without_error"
    return {
        "raw_content": content,
        "raw_tool_calls": [{"name": t["name"], "args": t["args"]} for t in raw.tool_calls],
        "json_parsed_strict": candidate is not None,
        "parsed": parsed.model_dump(mode="json") if parsed is not None else None,
        "parsing_error": err_name(out["parsing_error"]),
        "parsing_error_message": str(out["parsing_error"])[:400] if out["parsing_error"] else None,
        "outcome": outcome,
        "schema_valid": valid,
        "schema_error": why,
    }


def extract_plain(client: httpx.Client, base_url: str, model: str, text: str) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": fleet.extraction_messages(text),
        "stream": False,
        "format": fleet.DeliveryException.model_json_schema(),
        "options": dict(SAMPLING),
    }
    r = client.post(f"{base_url}/api/chat", json=body)
    r.raise_for_status()
    content = r.json()["message"]["content"]
    candidate = raw_json(content)
    valid, why = schema_valid(fleet.DeliveryException, candidate)
    parsed = fleet.DeliveryException.model_validate(candidate).model_dump(mode="json") if valid else None
    return {
        "raw_content": content,
        "raw_tool_calls": [],
        "json_parsed_strict": candidate is not None,
        "parsed": parsed,
        "parsing_error": None
        if valid
        else "pydantic.ValidationError"
        if candidate is not None
        else "json.JSONDecodeError",
        "parsing_error_message": why,
        "outcome": "parsed" if valid else "error",
        "schema_valid": valid,
        "schema_error": why,
    }


def run_extractions(models: list[str], methods: list[str], limit: int | None, out_dir) -> list[dict]:
    proxy = LoggingProxy(OLLAMA_URL, log_path=out_dir / "wire.jsonl").start()
    cases = fleet.CASES[:limit] if limit else fleet.CASES
    rows = []
    try:
        with httpx.Client(timeout=300) as client:
            for model in models:
                stop_models([m for m in MODELS if m != model])  # one model loaded at a time
                for method in methods:
                    for cid, text, expected in cases:
                        proxy.take()
                        proxy.label = f"{model}:{method}:{cid}"
                        t0 = time.perf_counter()
                        try:
                            if method == "plain_json_schema":
                                rec = extract_plain(client, proxy.url, model, text)
                            else:
                                rec = extract_langchain(proxy.url, model, method, text)
                            rec["exception"] = None
                        except Exception as e:  # include_raw=True should catch parse errors; anything else lands here
                            rec = {"outcome": f"exception:{type(e).__name__}", "parsed": None, "schema_valid": False}
                            rec["exception"] = f"{err_name(e)}: {str(e)[:300]}"
                        rec["elapsed_s"] = round(time.perf_counter() - t0, 3)
                        rec["requests"] = len([x for x in proxy.take() if x.path == "/api/chat"])
                        rec.update({"model": model, "method": method, "case": cid, "expected": expected})
                        rec["grade"] = grade(rec.get("parsed"), expected)
                        rows.append(rec)
                        g = rec["grade"]
                        print(
                            f"{model:12} {method:18} {cid}  {rec['outcome']:28} fields {g['correct']}/{g['total']}"
                            f"  requests {rec['requests']}"
                        )
                stop_models([model])
    finally:
        proxy.stop()
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["method"])].append(r)
    table = []
    for (model, method), rs in groups.items():
        per_field = {f: sum(r["grade"]["fields"][f] for r in rs) for f in FIELDS}
        table.append(
            {
                "model": model,
                "method": method,
                "n": len(rs),
                "json_strict": sum(bool(r.get("json_parsed_strict")) for r in rs),
                "parsed": sum(r.get("parsed") is not None for r in rs),
                "schema_valid": sum(bool(r.get("schema_valid")) for r in rs),
                "all_fields_correct": sum(r["grade"]["all_correct"] for r in rs),
                "fields_correct": sum(r["grade"]["correct"] for r in rs),
                "fields_total": sum(r["grade"]["total"] for r in rs),
                "per_field": per_field,
                "outcomes": dict(Counter(r["outcome"] for r in rs)),
                "max_requests_per_extraction": max(r["requests"] for r in rs),
            }
        )
    return table


def print_summary(table: list[dict]) -> None:
    print(
        f"\n{'model':12} {'method':18} {'n':>3} {'JSON':>5} {'parsed':>6} {'valid':>5} {'all ok':>6} "
        f"{'fields':>7}  {'id':>3} {'city':>4} {'rsn':>3} {'hrs':>3}  outcomes"
    )
    for t in table:
        pf = t["per_field"]
        print(
            f"{t['model']:12} {t['method']:18} {t['n']:>3} {t['json_strict']:>5} {t['parsed']:>6} "
            f"{t['schema_valid']:>5} {t['all_fields_correct']:>6} {t['fields_correct']:>3}/{t['fields_total']:<3}  "
            f"{pf['package_id']:>3} {pf['city']:>4} {pf['reason']:>3} {pf['delay_hours']:>3}  {t['outcomes']}"
        )


# --------------------------------------------------------------------------- failure modes (stub)

BAD_ENUM = {"package_id": "PKG-10432", "city": "Leeds", "reason": "flat_tyre", "delay_hours": 4}
GOOD = {"package_id": "PKG-10432", "city": "Leeds", "reason": "vehicle_breakdown", "delay_hours": 4}

# (name, reply content, reply tool_calls)
FAILURE_REPLIES = [
    ("prose, not JSON", "Sorry, I can't extract that.", None),
    ("JSON with an enum value outside the schema", json.dumps(BAD_ENUM), None),
    ("valid JSON inside a markdown fence", "```json\n" + json.dumps(GOOD) + "\n```", None),
    ("JSON missing a required field", json.dumps({"package_id": "PKG-10432", "city": "Leeds"}), None),
    (
        "tool call with an enum value outside the schema",
        "",
        [{"function": {"name": "DeliveryException", "arguments": BAD_ENUM}}],
    ),
    ("tool call to an unknown tool name", "", [{"function": {"name": "something_else", "arguments": GOOD}}]),
]


def failure_modes() -> list[dict]:
    rows = []
    with StubOllama() as stub:
        llm = ChatOllama(model="stub-model", base_url=stub.url)
        for name, content, tool_calls in FAILURE_REPLIES:
            # A tool-call reply only means something for function_calling.
            for method in ["function_calling"] if tool_calls else ["json_schema", "function_calling", "json_mode"]:
                for include_raw in (False, True):
                    stub.reset()
                    stub.content, stub.tool_calls = content, tool_calls
                    runnable = llm.with_structured_output(
                        fleet.DeliveryException, method=method, include_raw=include_raw
                    )
                    try:
                        res = runnable.invoke("x")
                        if include_raw:
                            result = {
                                "parsed": repr(res["parsed"]),
                                "parsing_error": err_name(res["parsing_error"]),
                            }
                        else:
                            result = {"returned": repr(res)}
                        raised = None
                    except Exception as e:
                        result, raised = None, f"{err_name(e)}: {str(e).splitlines()[0][:160]}"
                    rows.append(
                        {
                            "reply": name,
                            "method": method,
                            "include_raw": include_raw,
                            "raised": raised,
                            "result": result,
                            "stub_requests": stub.requests,
                        }
                    )
    return rows


def print_failure_modes(rows: list[dict]) -> None:
    print("\nFailure modes against the stub (each row: one invoke; 'requests' = requests the stub received)")
    for r in rows:
        what = f"raised {r['raised']}" if r["raised"] else f"returned {r['result']}"
        print(
            f"  {r['reply'][:44]:44} {r['method']:16} include_raw={r['include_raw']!s:5} requests={r['stub_requests']}  {what}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--methods", nargs="+", default=METHODS, choices=METHODS)
    ap.add_argument("--limit", type=int, help="only the first N cases")
    ap.add_argument("--failure-modes-only", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    refuse_if_tracing()
    out = run_dir("structured", args.out)

    fm = failure_modes()
    print_failure_modes(fm)
    result: dict[str, Any] = {"failure_modes": fm}
    if not args.failure_modes_only:
        env = environment(args.models)
        rows = run_extractions(args.models, args.methods, args.limit, out)
        table = summarise(rows)
        print_summary(table)
        result.update({"environment": env, "rows": rows, "summary": table, "sampling": SAMPLING})
    write_json(out / "results.json", result)
    print(f"\nSaved {out / 'results.json'}")


if __name__ == "__main__":
    main()
