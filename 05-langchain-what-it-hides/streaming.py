"""Measurement 5: streaming, callbacks, and token counts, LangChain against the raw stream.

Through the logging proxy, so each LangChain call can be compared with the exact NDJSON lines that
came over the wire for that same request:

1. ``ChatOllama.stream()``: chunks yielded vs lines on the wire, the joined text, and where the token
   counts end up (``usage_metadata`` vs Ollama's ``prompt_eval_count`` / ``eval_count``).
2. ``ChatOllama.invoke()`` with a callback handler: what goes over the wire for a non-streaming call,
   and how many token callbacks fire.
3. A plain httpx streamed request with the same body, for the text (a separate generation, with the
   same temperature 0 and seed).

    python streaming.py                      # qwen2.5:3b, 3 requests
    python streaming.py --models qwen2.5:3b llama3.2:3b llama3.2:1b
    python streaming.py --stub               # no model
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

import fleet
from common import SAMPLING, environment, refuse_if_tracing, run_dir, stop_models, write_json
from wire import OLLAMA_URL, LoggingProxy, StubOllama


class TokenCounter(BaseCallbackHandler):
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.llm_end_usage: Any = None

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.tokens.append(token)

    def on_llm_end(self, response, **kwargs) -> None:
        gen = response.generations[0][0]
        self.llm_end_usage = getattr(getattr(gen, "message", None), "usage_metadata", None)


def messages() -> list:
    m = fleet.chat_messages()
    return [SystemMessage(m[0]["content"]), HumanMessage(m[1]["content"])]


def wire_summary(lines: list[dict]) -> dict[str, Any]:
    final = next((x for x in reversed(lines) if isinstance(x, dict) and x.get("done")), {})
    return {
        "lines": len(lines),
        "lines_with_content": sum(1 for x in lines if isinstance(x, dict) and x.get("message", {}).get("content")),
        "text": "".join(x.get("message", {}).get("content", "") for x in lines if isinstance(x, dict)),
        "final_line": {k: v for k, v in final.items() if k != "message"},
    }


def run_model(proxy: LoggingProxy, model: str, client: httpx.Client) -> dict[str, Any]:
    res: dict[str, Any] = {"model": model}
    llm = ChatOllama(model=model, base_url=proxy.url, **SAMPLING)

    # 1. .stream()
    proxy.take()
    chunks = list(llm.stream(messages()))
    ex = [x for x in proxy.take() if x.path == "/api/chat"][-1]
    wire = wire_summary(ex.response_lines)
    lc_text = "".join(c.content for c in chunks)
    res["stream"] = {
        "request_stream_field": ex.request_body.get("stream"),
        "chunks_yielded": len(chunks),
        "chunks_with_content": sum(1 for c in chunks if c.content),
        "wire": wire,
        "same_text_as_wire": lc_text == wire["text"],
        "text": lc_text,
        "chunks_with_usage_metadata": sum(1 for c in chunks if c.usage_metadata),
        "last_chunk_usage_metadata": chunks[-1].usage_metadata if chunks else None,
        "last_chunk_response_metadata": chunks[-1].response_metadata if chunks else None,
    }
    full = None
    for c in chunks:
        full = c if full is None else full + c
    res["stream"]["aggregated_usage_metadata"] = full.usage_metadata if full is not None else None

    # 2. .invoke() with callbacks
    counter = TokenCounter()
    msg = llm.invoke(messages(), config={"callbacks": [counter]})
    ex = [x for x in proxy.take() if x.path == "/api/chat"][-1]
    wire = wire_summary(ex.response_lines)
    res["invoke"] = {
        "request_stream_field": ex.request_body.get("stream"),
        "wire_lines": wire["lines"],
        "on_llm_new_token_calls": len(counter.tokens),
        "callback_text_equals_reply": "".join(counter.tokens) == msg.content,
        "same_text_as_wire": msg.content == wire["text"],
        "usage_metadata": msg.usage_metadata,
        "on_llm_end_usage_metadata": counter.llm_end_usage,
        "response_metadata": msg.response_metadata,
        "wire_final_line": wire["final_line"],
    }

    # 3. plain streamed request, same body as LangChain's
    body = {"model": model, "messages": fleet.chat_messages(), "stream": True, "options": dict(SAMPLING)}
    lines = []
    with client.stream("POST", f"{proxy.url}/api/chat", json=body) as r:
        for line in r.iter_lines():
            if line.strip():
                lines.append(json.loads(line))
    proxy.take()
    plain = wire_summary(lines)
    res["plain_stream"] = {
        "lines": plain["lines"],
        "text": plain["text"],
        "same_text_as_langchain_stream": plain["text"] == lc_text,
        "final_line": plain["final_line"],
    }
    return res


def print_result(r: dict[str, Any]) -> None:
    s, i, p = r["stream"], r["invoke"], r["plain_stream"]
    print(f"\n=== {r['model']}")
    print(
        f"  .stream(): request stream={s['request_stream_field']}; {s['wire']['lines']} lines on the wire "
        f"({s['wire']['lines_with_content']} with text); {s['chunks_yielded']} chunks yielded "
        f"({s['chunks_with_content']} with text); same text as the wire: {s['same_text_as_wire']}"
    )
    fl = s["wire"]["final_line"]
    print(
        f"  tokens: wire prompt_eval_count={fl.get('prompt_eval_count')} eval_count={fl.get('eval_count')}; "
        f"LangChain usage_metadata on {s['chunks_with_usage_metadata']} chunk(s), last = {s['last_chunk_usage_metadata']}"
    )
    print(f"  aggregated (chunk + chunk ...) usage_metadata = {s['aggregated_usage_metadata']}")
    print(
        f"  .invoke(): request stream={i['request_stream_field']}; {i['wire_lines']} lines on the wire; "
        f"on_llm_new_token fired {i['on_llm_new_token_calls']} times; same text as the wire: {i['same_text_as_wire']}"
    )
    print(f"  invoke usage_metadata = {i['usage_metadata']}")
    print(f"  invoke response_metadata keys = {sorted(i['response_metadata'])}")
    print(
        f"  plain stream: {p['lines']} lines; same text as the LangChain stream: {p['same_text_as_langchain_stream']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["qwen2.5:3b"])
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    refuse_if_tracing()
    out = run_dir("streaming", args.out)
    stub = StubOllama().start() if args.stub else None
    upstream = stub.url if stub else OLLAMA_URL
    proxy = LoggingProxy(upstream, log_path=out / "wire.jsonl").start()
    results = []
    try:
        with httpx.Client(timeout=300) as client:
            for model in args.models:
                r = run_model(proxy, model, client)
                print_result(r)
                results.append(r)
                if not stub:
                    stop_models([model])
    finally:
        proxy.stop()
        if stub:
            stub.stop()
    write_json(
        out / "results.json",
        {"environment": environment(args.models, upstream), "stub": bool(stub), "results": results},
    )
    print(f"\nSaved {out / 'results.json'}")


if __name__ == "__main__":
    main()
