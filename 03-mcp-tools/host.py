"""A minimal MCP host: connects to MCP servers, hands their tools to an Ollama model, and runs the tool loop.

    python host.py "What is the status of PKG-00042?"
    python host.py --model llama3.2:3b --mode legacy "How many packages went from Berlin to Munich?"
    python host.py --url http://127.0.0.1:8765/mcp "..."     # the same server over Streamable HTTP

MCP standardises the conversation between this host's clients and the servers (discover or initialize,
tools/list, tools/call, ...). Everything else here is the host's own choice, not MCP: how tool definitions
are shown to the model (Ollama's `tools` field), the system prompt, how many tool rounds to allow, what to
do with an error, and whether to run a call at all (`policy`).

Every JSON-RPC message between client and server is recorded (direction, method, byte size) by wrapping
the transport, and can be written to a JSONL file with --log.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import anyio
import anyio.to_thread
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import Implementation

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
# num_predict caps a reply: without it, one llama3.2:1b reply ran past the 300-second timeout in both runs.
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1024}
MAX_TOOL_ROUNDS = 4
CLIENT_INFO = Implementation(name="fleet-host", version="1.0.0")

SYSTEM_PROMPT = (
    "You are a fleet-operations assistant for a delivery company. Use the tools to look up packages, devices, "
    "and delivery exceptions. Answer in one or two sentences and include the exact values the tools return."
)


# --------------------------------------------------------------------------------------------------
# JSON-RPC log: wrap a transport's two streams and record every message that passes through.
# --------------------------------------------------------------------------------------------------

def wire_json(message: SessionMessage) -> str:
    """The JSON text of a message, serialised the way the SDK's stdio transports write it (one line each)."""
    return message.message.model_dump_json(by_alias=True, exclude_unset=True)


def describe(payload: dict[str, Any]) -> tuple[str, str | None]:
    """(kind, method) for a JSON-RPC payload. Responses carry no method; the log fills it in from the request id."""
    if "method" in payload:
        return ("request" if "id" in payload else "notification"), payload["method"]
    return ("error" if "error" in payload else "response"), None


@dataclass
class RpcLog:
    """Every JSON-RPC message, in order: direction, kind, method, id, size in bytes, and (optionally) the body."""

    keep_bodies: bool = False
    entries: list[dict[str, Any]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)
    _pending: dict[tuple[str, Any], str] = field(default_factory=dict)

    def record(self, server: str, direction: str, text: str) -> None:
        payload = json.loads(text)
        kind, method = describe(payload)
        msg_id = payload.get("id")
        if kind == "request":
            self._pending[(server, msg_id)] = method
        elif kind in ("response", "error"):
            method = self._pending.pop((server, msg_id), None)
        entry = {
            "t_ms": round((time.perf_counter() - self._t0) * 1000, 3),
            "server": server,
            "dir": direction,  # "client->server" or "server->client"
            "kind": kind,
            "method": method,
            "id": msg_id,
            "bytes": len(text.encode()) + 1,  # + the newline that frames each message on stdio
        }
        if self.keep_bodies:
            entry["body"] = payload
        self.entries.append(entry)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for entry in self.entries:
                f.write(json.dumps(entry) + "\n")


class _LoggedRead:
    def __init__(self, inner: Any, log: RpcLog, server: str) -> None:
        self._inner, self._log, self._server = inner, log, server

    def _seen(self, item: Any) -> Any:
        if isinstance(item, SessionMessage):
            self._log.record(self._server, "server->client", wire_json(item))
        return item

    async def receive(self) -> Any:
        return self._seen(await self._inner.receive())

    def __aiter__(self) -> _LoggedRead:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _LoggedRead:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def __getattr__(self, name: str) -> Any:  # e.g. last_context, which the dispatcher reads if present
        return getattr(self._inner, name)


class _LoggedWrite:
    def __init__(self, inner: Any, log: RpcLog, server: str) -> None:
        self._inner, self._log, self._server = inner, log, server

    async def send(self, item: SessionMessage) -> None:
        self._log.record(self._server, "client->server", wire_json(item))
        await self._inner.send(item)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _LoggedWrite:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@asynccontextmanager
async def logged(transport: Any, log: RpcLog | None, server: str):
    """Wrap an MCP client transport so every message is recorded in `log` (or pass it through when log is None)."""
    async with transport as (read, write):
        if log is None:
            yield read, write
        else:
            yield _LoggedRead(read, log, server), _LoggedWrite(write, log, server)


# --------------------------------------------------------------------------------------------------
# Servers and the host
# --------------------------------------------------------------------------------------------------

@dataclass
class ServerSpec:
    """How to reach one MCP server. `trusted` is the host's decision; MCP only says untrusted hints are untrusted."""

    name: str
    command: list[str] | None = None  # stdio: launch this as a subprocess
    url: str | None = None  # Streamable HTTP
    trusted: bool = True


def fleet_server(*extra: str, name: str = "fleet") -> ServerSpec:
    return ServerSpec(name=name, command=[PYTHON, str(HERE / "fleet_server.py"), *extra])


def mcp_tool_to_ollama(tool: dict[str, Any]) -> dict[str, Any]:
    """An MCP tool definition (as on the wire) as an Ollama/OpenAI-style function tool.

    The JSON Schema is passed through unchanged; only the envelope differs. MCP's `annotations`,
    `outputSchema`, `title`, and `_meta` are not shown to the model.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
        },
    }


def tool_result_text(result: dict[str, Any]) -> str:
    """What the model sees of a tools/call result: the text blocks, marked when the server flagged an error."""
    parts = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
    other = [block.get("type") for block in result.get("content", []) if block.get("type") != "text"]
    if other:
        parts.append(f"[{len(other)} non-text content block(s) omitted: {', '.join(other)}]")
    text = "\n".join(parts)
    return f"ERROR: {text}" if result.get("isError") else text


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Ollama returns arguments as an object; some models put a JSON string there instead."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {"_raw": raw}
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {}


Policy = Callable[[str, dict[str, Any], dict[str, Any], ServerSpec], str | None]
"""(tool name, arguments, MCP tool definition, server) -> None to allow, or a reason to deny."""


def allow_all(name: str, args: dict[str, Any], tool: dict[str, Any], server: ServerSpec) -> str | None:
    return None


def deny_destructive(name: str, args: dict[str, Any], tool: dict[str, Any], server: ServerSpec) -> str | None:
    """A stand-in for a human confirming each call: deny anything not declared read-only by a trusted server.

    Annotations from an untrusted server are ignored, as the spec requires, so all its tools are denied.
    """
    if not server.trusted:
        return f"denied: {server.name} is not a trusted server"
    if not (tool.get("annotations") or {}).get("readOnlyHint", False):
        return "denied: the tool is not read-only, and no user is here to confirm it"
    return None


def ollama_chat(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                options: dict[str, Any] | None = None, url: str = OLLAMA_URL, timeout: float = 300) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": options or OPTIONS}
    if tools:
        body["tools"] = tools
    request = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class Host:
    """One MCP client per server (the spec's 1:1 client-server relationship), and one merged tool registry."""

    def __init__(self, servers: list[ServerSpec], log: RpcLog | None = None, mode: str = "auto",
                 errlog: TextIO | None = None) -> None:
        self.servers = servers
        self.log = log
        self.mode = mode
        self.errlog = errlog
        self.clients: dict[str, Client] = {}
        self.tools: dict[str, tuple[ServerSpec, dict[str, Any]]] = {}
        self.timings: dict[str, float] = {}
        self._stack: AsyncExitStack | None = None

    def _transport(self, spec: ServerSpec) -> Any:
        if spec.url:
            inner = streamable_http_client(spec.url)
        else:
            assert spec.command, spec
            params = StdioServerParameters(command=spec.command[0], args=spec.command[1:], cwd=str(HERE))
            inner = stdio_client(params, errlog=self.errlog or sys.stderr)
        return logged(inner, self.log, spec.name)

    async def __aenter__(self) -> Host:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for spec in self.servers:
            t0 = time.perf_counter()
            client = await self._stack.enter_async_context(
                Client(self._transport(spec), mode=self.mode, client_info=CLIENT_INFO))
            self.timings[f"connect:{spec.name}"] = time.perf_counter() - t0
            self.clients[spec.name] = client
            t0 = time.perf_counter()
            listed = await client.list_tools()
            self.timings[f"tools/list:{spec.name}"] = time.perf_counter() - t0
            for tool in listed.tools:
                definition = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                if definition["name"] in self.tools:
                    # MCP does not namespace tool names across servers; the host has to decide. This one refuses.
                    raise RuntimeError(f"tool name {definition['name']!r} is offered by two servers")
                self.tools[definition["name"]] = (spec, definition)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        assert self._stack is not None
        await self._stack.__aexit__(*exc)

    def protocol_versions(self) -> dict[str, str]:
        return {name: client.protocol_version for name, client in self.clients.items()}

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [mcp_tool_to_ollama(definition) for _, definition in self.tools.values()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec, _ = self.tools[name]
        result = await self.clients[spec.name].call_tool(name, arguments)
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def ask(self, question: str, model: str = DEFAULT_MODEL, policy: Policy = allow_all,
                  max_rounds: int = MAX_TOOL_ROUNDS, system_prompt: str = SYSTEM_PROMPT,
                  options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one question through the tool loop. Returns the answer and a record of every step."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt},
                                          {"role": "user", "content": question}]
        tools = self.ollama_tools()
        steps: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        answer, stop = "", "answered"
        for round_no in range(max_rounds + 1):
            t0 = time.perf_counter()
            # The final round goes without tools, so the model has to answer with what it has.
            response = await anyio.to_thread.run_sync(
                ollama_chat, model, messages, tools if round_no < max_rounds else None, options)
            message = response.get("message", {})
            steps.append({
                "round": round_no,
                "seconds": round(time.perf_counter() - t0, 3),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls"),
            })
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = message.get("content", "")
                if round_no == max_rounds:
                    stop = "round_cap"
                break
            messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})
            for call in tool_calls:
                function = call.get("function", {})
                name, args = function.get("name", ""), parse_arguments(function.get("arguments"))
                record: dict[str, Any] = {"round": round_no, "name": name, "arguments": args}
                if name not in self.tools:
                    text = f"ERROR: there is no tool named {name!r}."
                    record["outcome"] = "unknown_tool"
                else:
                    spec, definition = self.tools[name]
                    record["server"] = spec.name
                    reason = policy(name, args, definition, spec)
                    if reason:
                        text = f"ERROR: the host did not run this call ({reason})."
                        record["outcome"] = "denied"
                    else:
                        t1 = time.perf_counter()
                        result = await self.call_tool(name, args)
                        record["mcp_seconds"] = round(time.perf_counter() - t1, 4)
                        record["outcome"] = "error" if result.get("isError") else "ok"
                        text = tool_result_text(result)
                record["result"] = text
                calls.append(record)
                messages.append({"role": "tool", "content": text, "tool_name": name})
        return {"question": question, "model": model, "answer": answer, "stop": stop, "calls": calls, "steps": steps}


async def _main(args: argparse.Namespace) -> int:
    log = RpcLog(keep_bodies=args.bodies) if args.log else None
    spec = ServerSpec(name="fleet", url=args.url) if args.url else fleet_server()
    policy = deny_destructive if args.policy == "deny-destructive" else allow_all
    async with Host([spec], log=log, mode=args.mode) as host:
        print(f"protocol {host.protocol_versions()}  tools {list(host.tools)}", file=sys.stderr)
        result = await host.ask(args.question, model=args.model, policy=policy, max_rounds=args.max_rounds)
    for call in result["calls"]:
        print(f"  tool {call['name']}({json.dumps(call['arguments'])}) -> {call['outcome']}", file=sys.stderr)
    print(result["answer"])
    if log is not None:
        log.write(args.log)
        print(f"{len(log.entries)} JSON-RPC messages -> {args.log}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--mode", default="auto", help="'auto' (SDK default: server/discover, then fall back) or 'legacy'")
    parser.add_argument("--url", help="connect over Streamable HTTP instead of launching the server over stdio")
    parser.add_argument("--policy", choices=["allow-all", "deny-destructive"], default="allow-all")
    parser.add_argument("--max-rounds", type=int, default=MAX_TOOL_ROUNDS)
    parser.add_argument("--log", type=Path, help="write the JSON-RPC log to this JSONL file")
    parser.add_argument("--bodies", action="store_true", help="include each message body in the log")
    return anyio.run(_main, parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
