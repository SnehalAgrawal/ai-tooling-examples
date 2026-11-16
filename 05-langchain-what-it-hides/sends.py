"""Measurement 1: what a LangChain call puts on the wire, against the same request written by hand.

Every request goes through a logging proxy. For each call type, the LangChain request body (as
recorded on the wire) is diffed against the plain body sent with httpx.

    python sends.py                         # real Ollama, qwen2.5:3b: about 22 requests
    python sends.py --models qwen2.5:3b llama3.2:3b
    python sends.py --stub                  # no model: a stub answers, the request bodies are the same
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_ollama import ChatOllama

import fleet
from analysis import format_diff, json_diff
from common import (
    SAMPLING,
    environment,
    plain_chat,
    refuse_if_tracing,
    run_dir,
    stop_models,
    write_json,
)
from wire import OLLAMA_URL, LoggingProxy, StubOllama


def lc_messages(msgs: list[dict[str, str]]) -> list:
    kinds = {"system": SystemMessage, "user": HumanMessage}
    return [kinds[m["role"]](content=m["content"]) for m in msgs]


def summarise(result: Any) -> Any:
    """A JSON-friendly summary of what a LangChain call returned."""
    if isinstance(result, AIMessage):
        return {
            "type": "AIMessage",
            "content": result.content[:300] if isinstance(result.content, str) else result.content,
            "tool_calls": result.tool_calls,
            "usage_metadata": result.usage_metadata,
            "response_metadata_keys": sorted(result.response_metadata),
        }
    if isinstance(result, dict) and "raw" in result:
        return {
            "parsed": summarise(result["parsed"]),
            "parsing_error": repr(result["parsing_error"]) if result["parsing_error"] else None,
            "raw": summarise(result["raw"]),
        }
    if hasattr(result, "model_dump"):
        return {"type": type(result).__name__, "value": result.model_dump(mode="json")}
    return result


@dataclass
class Case:
    name: str
    what: str
    langchain: Callable[[str, str], Any]  # (base_url, model) -> result
    plain: Callable[[str], dict] | None  # model -> body
    checks: dict[str, str] = field(default_factory=dict)  # parameter you set -> wire path where it should land


def extraction_text() -> str:
    return fleet.CASES[0][1]


def build_cases() -> list[Case]:
    chat = fleet.chat_messages()
    x_msgs = fleet.extraction_messages(extraction_text())
    schema = fleet.DeliveryException.model_json_schema()
    cases: list[Case] = []

    cases.append(
        Case(
            "chat",
            "A plain chat call, every parameter left at its default.",
            lambda url, m: ChatOllama(model=m, base_url=url).invoke(lc_messages(chat)),
            lambda m: {"model": m, "messages": chat, "stream": False},
        )
    )
    cases.append(
        Case(
            "sampling",
            "temperature=0, seed=42, num_ctx=8192 set on the ChatOllama constructor.",
            lambda url, m: ChatOllama(model=m, base_url=url, **SAMPLING).invoke(lc_messages(chat)),
            lambda m: {"model": m, "messages": chat, "stream": False, "options": dict(SAMPLING)},
            {"temperature": "options.temperature", "seed": "options.seed", "num_ctx": "options.num_ctx"},
        )
    )
    cases.append(
        Case(
            "sampling_plus_call_options",
            "The same constructor, plus options={'num_predict': 64} passed to invoke().",
            lambda url, m: ChatOllama(model=m, base_url=url, **SAMPLING).invoke(
                lc_messages(chat), options={"num_predict": 64}
            ),
            lambda m: {"model": m, "messages": chat, "stream": False, "options": {**SAMPLING, "num_predict": 64}},
            {
                "temperature": "options.temperature",
                "seed": "options.seed",
                "num_ctx": "options.num_ctx",
                "num_predict": "options.num_predict",
            },
        )
    )
    cases.append(
        Case(
            "constructor_args_not_fields",
            "ChatOllama(max_tokens=64, timeout=5, max_retries=2): names other chat models accept.",
            lambda url, m: ChatOllama(model=m, base_url=url, max_tokens=64, timeout=5, max_retries=2).invoke(
                lc_messages(chat)
            ),
            lambda m: {"model": m, "messages": chat, "stream": False, "options": {"num_predict": 64}},
            {"max_tokens": "options.num_predict"},
        )
    )
    cases.append(
        Case(
            "per_call_temperature",
            "invoke(messages, temperature=0): a sampling parameter passed per call.",
            lambda url, m: ChatOllama(model=m, base_url=url).invoke(lc_messages(chat), temperature=0),
            lambda m: {"model": m, "messages": chat, "stream": False, "options": {"temperature": 0}},
            {"temperature": "options.temperature"},
        )
    )
    tools = [fleet.get_package_status, fleet.list_delayed_packages]
    tool_msgs = [{"role": "user", "content": fleet.TOOL_QUESTION}]
    cases.append(
        Case(
            "tools",
            "bind_tools() with two Python functions (Google-style docstrings).",
            lambda url, m: ChatOllama(model=m, base_url=url, **SAMPLING).bind_tools(tools).invoke(fleet.TOOL_QUESTION),
            lambda m: {
                "model": m,
                "messages": tool_msgs,
                "tools": fleet.PLAIN_TOOLS,
                "stream": False,
                "options": dict(SAMPLING),
            },
        )
    )
    cases.append(
        Case(
            "tools_tool_choice",
            "bind_tools(..., tool_choice='get_package_status'): force a tool.",
            lambda url, m: (
                ChatOllama(model=m, base_url=url, **SAMPLING)
                .bind_tools(tools, tool_choice="get_package_status")
                .invoke(fleet.TOOL_QUESTION)
            ),
            lambda m: {
                "model": m,
                "messages": tool_msgs,
                "tools": fleet.PLAIN_TOOLS,
                "stream": False,
                "options": dict(SAMPLING),
            },
            {"tool_choice": "tool_choice"},
        )
    )

    history_plain = [
        {"role": "user", "content": fleet.TOOL_QUESTION},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_package_status", "arguments": {"package_id": "PKG-10432"}}}],
        },
        {"role": "tool", "tool_name": "get_package_status", "content": fleet.get_package_status("PKG-10432")},
    ]

    def round_trip(url: str, m: str) -> Any:
        history = [
            HumanMessage(fleet.TOOL_QUESTION),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "get_package_status", "args": {"package_id": "PKG-10432"}}],
            ),
            ToolMessage(content=fleet.get_package_status("PKG-10432"), tool_call_id="call_1"),
        ]
        return ChatOllama(model=m, base_url=url, **SAMPLING).bind_tools(tools).invoke(history)

    cases.append(
        Case(
            "tool_result_round_trip",
            "The second turn of a tool call: the model's tool call and the tool's result sent back.",
            round_trip,
            lambda m: {
                "model": m,
                "messages": history_plain,
                "tools": fleet.PLAIN_TOOLS,
                "stream": False,
                "options": dict(SAMPLING),
            },
            {"tool_call_id": "messages[2].tool_call_id"},
        )
    )

    def structured(method: str | None) -> Callable[[str, str], Any]:
        def run(url: str, m: str) -> Any:
            llm = ChatOllama(model=m, base_url=url, **SAMPLING)
            kw = {} if method is None else {"method": method}
            return llm.with_structured_output(fleet.DeliveryException, include_raw=True, **kw).invoke(
                lc_messages(x_msgs)
            )

        return run

    base = {"messages": x_msgs, "stream": False, "options": dict(SAMPLING)}
    cases.append(
        Case(
            "structured_default",
            "with_structured_output(DeliveryException), no method given.",
            structured(None),
            lambda m: {"model": m, **base, "format": schema},
        )
    )
    cases.append(
        Case(
            "structured_json_schema",
            "with_structured_output(..., method='json_schema').",
            structured("json_schema"),
            lambda m: {"model": m, **base, "format": schema},
        )
    )
    cases.append(
        Case(
            "structured_function_calling",
            "with_structured_output(..., method='function_calling').",
            structured("function_calling"),
            lambda m: {
                "model": m,
                **base,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "DeliveryException",
                            "description": fleet.DeliveryException.__doc__,
                            "parameters": schema,
                        },
                    }
                ],
            },
        )
    )
    cases.append(
        Case(
            "structured_json_mode",
            "with_structured_output(..., method='json_mode').",
            structured("json_mode"),
            lambda m: {"model": m, **base, "format": "json"},
        )
    )
    return cases


def get_path(obj: Any, path: str) -> Any:
    """Read ``a.b[2].c`` from a JSON value; KeyError if it is not there."""
    cur = obj
    for part in path.replace("]", "").replace("[", ".").split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def run_case(case: Case, proxy: LoggingProxy, model: str, client: httpx.Client) -> dict[str, Any]:
    out: dict[str, Any] = {"case": case.name, "what": case.what, "model": model}
    proxy.take()
    proxy.label = f"{case.name}:langchain"
    try:
        out["langchain_result"] = summarise(case.langchain(proxy.url, model))
        out["langchain_error"] = None
    except Exception as e:  # recorded, not hidden: this is one of the things being measured
        out["langchain_result"] = None
        out["langchain_error"] = f"{type(e).__module__}.{type(e).__name__}: {str(e)[:300]}"
    lc = [x for x in proxy.take() if x.path == "/api/chat"]
    out["langchain_requests"] = len(lc)
    lc_body = lc[-1].request_body if lc else None
    lc_headers = lc[-1].request_headers if lc else None

    plain_body = case.plain(model) if case.plain else None
    proxy.label = f"{case.name}:plain"
    plain_resp = plain_chat(client, proxy.url, plain_body) if plain_body else None
    pl = [x for x in proxy.take() if x.path == "/api/chat"]
    wire_plain = pl[-1].request_body if pl else None

    out["langchain_body"] = lc_body
    out["plain_body"] = wire_plain
    out["plain_reply"] = (plain_resp or {}).get("message")
    out["langchain_headers"] = lc_headers
    out["plain_headers"] = pl[-1].request_headers if pl else None
    out["diff"] = json_diff(lc_body, wire_plain) if lc_body is not None and wire_plain is not None else None
    if case.name.startswith("tool") or case.name == "structured_function_calling":
        # Attribute the tool differences: what LangChain generated, against what reached the wire.
        generated = (
            [convert_to_openai_tool(fleet.DeliveryException)]
            if case.name == "structured_function_calling"
            else [convert_to_openai_tool(f) for f in (fleet.get_package_status, fleet.list_delayed_packages)]
        )
        out["tools_generated_vs_wire"] = json_diff(generated, (lc_body or {}).get("tools", []))
    checks = {}
    for param, path in case.checks.items():
        try:
            checks[param] = {"path": path, "on_wire": True, "value": get_path(lc_body, path)}
        except (KeyError, IndexError, ValueError, TypeError):
            checks[param] = {"path": path, "on_wire": False}
    out["checks"] = checks
    # Tool-call IDs: the ones Ollama sent, against the ones LangChain handed back.
    wire_ids = [
        tc.get("id")
        for line in (lc[-1].response_lines if lc else [])
        if isinstance(line, dict)
        for tc in line.get("message", {}).get("tool_calls", [])
    ]
    res = out["langchain_result"] or {}
    lc_calls = (res.get("raw") or res).get("tool_calls") or [] if isinstance(res, dict) else []
    if wire_ids or lc_calls:
        out["tool_call_ids"] = {"on_the_wire": wire_ids, "langchain": [c.get("id") for c in lc_calls]}
    return out


def print_case(r: dict[str, Any]) -> None:
    print(f"\n=== {r['case']} ({r['model']}): {r['what']}")
    if r["langchain_error"]:
        print(f"  LangChain raised {r['langchain_error']}")
    print(f"  LangChain sent {r['langchain_requests']} request(s) to /api/chat")
    if r["diff"] is not None:
        for line in format_diff(r["diff"]):
            print(line)
    if "tools_generated_vs_wire" in r:
        print("  tools as LangChain generated them (convert_to_openai_tool) vs as they reached the wire:")
        for line in format_diff(r["tools_generated_vs_wire"], "generated", "wire"):
            print("  " + line)
    if r.get("tool_call_ids"):
        ids = r["tool_call_ids"]
        print(f"  tool-call IDs: from Ollama {ids['on_the_wire']}; in LangChain's AIMessage {ids['langchain']}")
    for param, c in r["checks"].items():
        state = f"on the wire at {c['path']} = {json.dumps(c.get('value'))}" if c["on_wire"] else "NOT on the wire"
        print(f"  check: {param}: {state}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["qwen2.5:3b"])
    ap.add_argument("--stub", action="store_true", help="forward to a local stub instead of Ollama")
    ap.add_argument("--cases", nargs="*", help="only these case names")
    ap.add_argument("--out")
    args = ap.parse_args()
    refuse_if_tracing()

    out = run_dir("sends", args.out)
    stub = StubOllama().start() if args.stub else None
    upstream = stub.url if stub else OLLAMA_URL
    proxy = LoggingProxy(upstream, log_path=out / "wire.jsonl").start()
    results = []
    try:
        with httpx.Client(timeout=300) as client:
            for model in args.models:
                for case in build_cases():
                    if args.cases and case.name not in args.cases:
                        continue
                    r = run_case(case, proxy, model, client)
                    print_case(r)
                    results.append(r)
    finally:
        proxy.stop()
        if stub:
            stub.stop()
        else:
            stop_models(args.models)

    # What LangChain generated for the function_calling tool, before ollama-python serialised it.
    extras = {
        "langchain_tool_for_DeliveryException": convert_to_openai_tool(fleet.DeliveryException),
        "langchain_tools_for_functions": [
            convert_to_openai_tool(f) for f in (fleet.get_package_status, fleet.list_delayed_packages)
        ],
        "pydantic_json_schema": fleet.DeliveryException.model_json_schema(),
    }
    write_json(
        out / "results.json",
        {"environment": environment(args.models, upstream), "stub": bool(stub), "results": results, "extras": extras},
    )
    print(f"\nSaved {out / 'results.json'} and {out / 'wire.jsonl'}")


if __name__ == "__main__":
    main()
