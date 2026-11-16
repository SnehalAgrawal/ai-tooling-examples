"""Measurement 1: one question end to end, every JSON-RPC message recorded, and what the tools cost in the prompt.

    python trace_protocol.py                                   # qwen2.5:3b, question q20 (two dependent tool calls)
    python trace_protocol.py --model llama3.2:3b --qid q01 --repeats 2

Writes to runs/trace-<time>/:
  rpc-<mode>.jsonl    every message of the model run (auto mode) and of a no-model run in legacy mode,
                      with direction, method, id, size in bytes, and the full body
  answer.json         the tool loop: every model request (prompt_eval_count, eval_count), call, and the answer
  prompt_tokens.json  prompt_eval_count for the same first request with no tools, the descriptive tools, and the
                      terse tools (num_predict 1; the model is unloaded before each request so no prompt
                      cache is reused), each repeated --repeats times
  setup.json          versions, model digest, options, and the command
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread

from gold import QUESTIONS, compute_gold, grade
from host import OPTIONS, SYSTEM_PROMPT, Host, RpcLog, fleet_server, ollama_chat
from runlib import read_jsonl, run_dir, setup, table, unload, write_json


def summarise_log(entries: list[dict[str, Any]]) -> list[list[Any]]:
    """Messages and bytes per method and direction, plus the share of client bytes spent on request _meta."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in entries:
        groups[(e["method"] or "?", e["dir"])].append(e["bytes"])
    rows = [[m, d, len(b), sum(b)] for (m, d), b in groups.items()]
    rows.append(["total", "", len(entries), sum(e["bytes"] for e in entries)])
    return rows


def meta_bytes(entries: list[dict[str, Any]]) -> tuple[int, int]:
    """(bytes of params._meta in client requests, bytes of all client requests)."""
    meta = total = 0
    for e in entries:
        if e["dir"] == "client->server" and "body" in e:
            total += e["bytes"]
            m = (e["body"].get("params") or {}).get("_meta")
            if m is not None:
                meta += len(json.dumps({"_meta": m}, separators=(",", ":")).encode())
    return meta, total


async def protocol_only(mode: str) -> RpcLog:
    """Connect, list tools, make one call, close: no model involved."""
    log = RpcLog(keep_bodies=True)
    async with Host([fleet_server()], log=log, mode=mode) as host:
        await host.call_tool("get_package_status", {"package_id": "PKG-00012"})
    return log


async def run(args: argparse.Namespace) -> int:
    out = run_dir("trace", args.out)
    question = next(q for q in compute_gold() if q.qid == args.qid)
    write_json(out / "setup.json", setup([args.model], question=question.text, qid=question.qid,
                                         max_rounds=args.max_rounds))
    unload(args.model)

    # 1. The full exchange, with the model, in the SDK's default mode.
    log = RpcLog(keep_bodies=True)
    with (out / "server-stderr.log").open("w") as errlog:
        async with Host([fleet_server()], log=log, mode="auto", errlog=errlog) as host:
            versions = host.protocol_versions()
            tools = host.ollama_tools()
            result = await host.ask(question.text, model=args.model, max_rounds=args.max_rounds)
    result["grade"] = grade(question, result["calls"], result["answer"])
    result["gold"] = question.values
    result["protocol_versions"] = versions
    log.write(out / "rpc-auto.jsonl")
    write_json(out / "answer.json", result)

    # 2. The same protocol steps in legacy mode (initialize handshake), without a model.
    legacy = await protocol_only("legacy")
    legacy.write(out / "rpc-legacy.jsonl")

    # 3. What the tool definitions cost in the prompt: same first request, with and without tools.
    async with Host([fleet_server("--descriptions", "terse")]) as host:
        terse_tools = host.ollama_tools()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question.text}]
    one_token = {**OPTIONS, "num_predict": 1}
    counts: dict[str, list[int]] = {"no_tools": [], "descriptive_tools": [], "terse_tools": []}
    for _ in range(args.repeats):
        for label, t in (("no_tools", None), ("descriptive_tools", tools), ("terse_tools", terse_tools)):
            # Ollama reuses the KV cache for a shared prompt prefix, and prompt_eval_count then counts only the
            # tokens it evaluated (in the tool loop above, the second request reports fewer tokens than the
            # first although it contains it). Unloading first makes every count the full prompt.
            unload(args.model)
            response = await anyio.to_thread.run_sync(ollama_chat, args.model, messages, t, one_token)
            counts[label].append(response.get("prompt_eval_count"))
    tools_json = json.dumps(tools, separators=(",", ":"))
    terse_json = json.dumps(terse_tools, separators=(",", ":"))
    prompt_tokens = {
        "model": args.model,
        "prompt_eval_count": counts,
        "tools_json_bytes": {"descriptive": len(tools_json.encode()), "terse": len(terse_json.encode())},
        "per_tool_json_bytes": {t["function"]["name"]: len(json.dumps(t, separators=(",", ":")).encode())
                                for t in tools},
    }
    write_json(out / "prompt_tokens.json", prompt_tokens)
    unload(args.model)

    # Report
    print(f"question {question.qid}: {question.text}")
    print(f"protocol version: {versions}")
    for c in result["calls"]:
        print(f"  call {c['name']}({json.dumps(c['arguments'])}) -> {c['outcome']}")
    print(f"answer: {result['answer']}")
    print(f"grade: {result['grade']}  gold: {question.values}")
    for s in result["steps"]:
        print(f"  model request {s['round']}: prompt_eval_count {s['prompt_eval_count']}, eval_count "
              f"{s['eval_count']}, {s['seconds']} s")
    for mode in ("auto", "legacy"):
        entries = read_jsonl(out / f"rpc-{mode}.jsonl")
        meta, total = meta_bytes(entries)
        print(f"\nJSON-RPC messages, {mode} mode ({entries[0]['method']} first):")
        print(table(summarise_log(entries), ["method", "direction", "messages", "bytes"]))
        print(f"client request bytes spent on params._meta: {meta} of {total}")
    print("\nprompt_eval_count for the first request:")
    for label, values in counts.items():
        print(f"  {label:18} {values}")
    print(f"tools JSON sent to Ollama: {prompt_tokens['tools_json_bytes']} bytes (compact)")
    print(f"\n-> {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--qid", default="q20", choices=[q.qid for q in QUESTIONS])
    parser.add_argument("--repeats", type=int, default=2, help="repeats of each prompt-token measurement")
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--out", type=Path)
    return anyio.run(run, parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
