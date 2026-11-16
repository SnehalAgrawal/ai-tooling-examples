"""Two local HTTP servers for looking at the wire.

- ``LoggingProxy`` sits between a client and Ollama (or the stub): it forwards every request unchanged,
  streams the response back line by line, and records the request body and every response line.
- ``StubOllama`` answers ``/api/chat`` like Ollama, instantly, with a canned reply. Its ``mode``
  switches it into the failure cases (500, malformed JSON, a slow reply, an error inside the stream),
  and it counts the requests it receives, which is how retries are measured.

Both run in a background thread of the calling process, on 127.0.0.1 and a free port, with the
standard library only, so they add no dependency that could change what is measured.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

OLLAMA_URL = "http://127.0.0.1:11434"

# Headers that describe one hop and must not be forwarded as they are.
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "proxy-connection"}


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparsed": raw.decode("utf-8", "replace")}


@dataclass
class Exchange:
    """One request through the proxy and everything that came back."""

    label: str
    method: str
    path: str
    request_headers: dict[str, str]
    request_body: Any
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_lines: list[Any] = field(default_factory=list)
    line_times_ms: list[float] = field(default_factory=list)
    error: str | None = None
    started: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "method": self.method,
            "path": self.path,
            "request_headers": self.request_headers,
            "request_body": self.request_body,
            "status": self.status,
            "response_headers": self.response_headers,
            "response_lines": self.response_lines,
            "line_times_ms": self.line_times_ms,
            "error": self.error,
        }


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Background:
    """Start a server in a thread; use as a context manager."""

    server: _Server

    def start(self) -> _Background:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# --------------------------------------------------------------------------- proxy


class LoggingProxy(_Background):
    """Forward to ``upstream`` and record each exchange; optionally append each to a JSONL file."""

    def __init__(self, upstream: str = OLLAMA_URL, log_path: Path | None = None) -> None:
        self.upstream = urlsplit(upstream)
        self.log_path = log_path
        self.exchanges: list[Exchange] = []
        self.label = ""
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence the default stderr log
                pass

            def _handle(self):
                proxy._forward(self)

            do_GET = do_POST = do_DELETE = do_HEAD = _handle

        self.server = _Server(("127.0.0.1", 0), Handler)

    def take(self) -> list[Exchange]:
        """Return and clear the exchanges recorded so far."""
        with self._lock:
            out, self.exchanges = self.exchanges, []
        return out

    def _record(self, ex: Exchange) -> None:
        with self._lock:
            self.exchanges.append(ex)
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a") as f:
                    f.write(json.dumps(ex.to_json()) + "\n")

    def _forward(self, h: BaseHTTPRequestHandler) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        ex = Exchange(
            label=self.label,
            method=h.command,
            path=h.path,
            request_headers={k.lower(): v for k, v in h.headers.items()},
            request_body=_parse_json(raw),
            started=time.perf_counter(),
        )
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Host"] = self.upstream.netloc
        conn = http.client.HTTPConnection(self.upstream.hostname, self.upstream.port, timeout=600)
        try:
            conn.request(h.command, h.path, body=raw or None, headers=headers)
            resp = conn.getresponse()
        except OSError as e:
            ex.error = f"{type(e).__name__}: {e}"
            self._record(ex)
            h.send_error(502, "upstream unreachable")
            return
        ex.status = resp.status
        ex.response_headers = {k.lower(): v for k, v in resp.getheaders()}
        h.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_HEADERS:
                h.send_header(k, v)
        h.send_header("Transfer-Encoding", "chunked")
        h.end_headers()
        recorded = False
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                ex.line_times_ms.append(round((time.perf_counter() - ex.started) * 1000, 2))
                ex.response_lines.append(_parse_json(line.strip()) if line.strip() else {"_blank": True})
                h.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                h.wfile.flush()
            # Record before the final chunk, so the exchange is there by the time the client has its reply.
            recorded = True
            self._record(ex)
            h.wfile.write(b"0\r\n\r\n")
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as e:
            ex.error = f"client went away: {type(e).__name__}"
        finally:
            conn.close()
            if not recorded:
                self._record(ex)


# --------------------------------------------------------------------------- stub


DEFAULT_REPLY = "Three packages are delayed: PKG-10432 in Leeds, PKG-10433 in York, and PKG-10440 in Hull."


def chat_lines(
    content: str = DEFAULT_REPLY,
    *,
    model: str = "stub-model",
    chunks: int = 8,
    tool_calls: list[dict] | None = None,
    prompt_eval_count: int = 42,
    eval_count: int | None = None,
) -> list[dict]:
    """The NDJSON objects Ollama sends for a streamed ``/api/chat`` reply, in order."""
    created = "2026-11-16T00:00:00Z"
    words = content.split(" ") if content else []
    size = max(1, -(-len(words) // chunks)) if words else 1
    parts = [" ".join(words[i : i + size]) for i in range(0, len(words), size)]
    parts = [p if i == 0 else " " + p for i, p in enumerate(parts)]
    lines: list[dict] = []
    for p in parts:
        lines.append(
            {"model": model, "created_at": created, "message": {"role": "assistant", "content": p}, "done": False}
        )
    if tool_calls:
        lines.append(
            {
                "model": model,
                "created_at": created,
                "message": {"role": "assistant", "content": "", "tool_calls": tool_calls},
                "done": False,
            }
        )
    lines.append(
        {
            "model": model,
            "created_at": created,
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "total_duration": 1000,
            "load_duration": 10,
            "prompt_eval_count": prompt_eval_count,
            "prompt_eval_duration": 100,
            "eval_count": eval_count if eval_count is not None else max(1, len(parts)),
            "eval_duration": 500,
        }
    )
    return lines


def chat_single(lines: list[dict]) -> dict:
    """Fold streamed lines into the one object Ollama returns when ``stream`` is false."""
    final = dict(lines[-1])
    content = "".join(line["message"].get("content", "") for line in lines)
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    calls = [c for line in lines for c in line["message"].get("tool_calls", [])]
    if calls:
        msg["tool_calls"] = calls
    final["message"] = msg
    return final


class StubOllama(_Background):
    """A fake Ollama ``/api/chat`` that answers instantly.

    ``mode``: ``ok`` (default), ``http500``, ``http404`` (Ollama's model-not-found), ``malformed``
    (200 with a body that is not JSON), ``slow`` (sleep ``delay_s`` first), ``stream_error`` (a streamed ``{"error": ...}`` line).
    ``content`` / ``tool_calls`` set the reply. ``requests`` counts every request received, and
    ``bodies`` keeps each request body.
    """

    def __init__(self, *, content: str = DEFAULT_REPLY, chunks: int = 8) -> None:
        self.mode = "ok"
        self.delay_s = 2.0
        self.content = content
        self.chunks = chunks
        self.tool_calls: list[dict] | None = None
        self.requests = 0
        self.bodies: list[Any] = []
        self.keep_bodies = True
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, json.dumps({"models": [{"name": "stub-model"}]}).encode())
                else:
                    self._send(200, b"Ollama is running", "text/plain")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with stub._lock:
                    stub.requests += 1
                    if stub.keep_bodies:
                        stub.bodies.append(_parse_json(raw))
                if self.path != "/api/chat":
                    self._send(404, b'{"error":"not found"}')
                    return
                stub._reply(self, raw)

        self.server = _Server(("127.0.0.1", 0), Handler)

    def reset(self, mode: str = "ok") -> None:
        with self._lock:
            self.mode = mode
            self.requests = 0
            self.bodies = []
            self.tool_calls = None

    def _reply(self, h, raw: bytes) -> None:
        mode = self.mode
        if mode == "http500":
            h._send(500, b'{"error":"stub: internal server error"}')
            return
        if mode == "http404":
            h._send(404, b'{"error":"model \'stub-model\' not found"}')
            return
        if mode == "malformed":
            h._send(
                200,
                b'{"model": "stub-model", "message": {"role": "assistant", "content": "trunc',
                "application/x-ndjson",
            )
            return
        if mode == "slow":
            time.sleep(self.delay_s)
        try:
            req = json.loads(raw) if raw else {}
        except ValueError:
            req = {}
        model = req.get("model") or "stub-model"
        stream = req.get("stream", True)  # Ollama streams unless told not to
        lines = chat_lines(self.content, model=model, chunks=self.chunks, tool_calls=self.tool_calls)
        if mode == "stream_error":
            if not stream:  # without streaming there is no line to put it in: answer with an HTTP error
                h._send(500, b'{"error":"stub: model runner stopped"}')
                return
            lines = lines[:2] + [{"error": "stub: model runner stopped"}]
        if stream:
            body = b"".join(json.dumps(line).encode() + b"\n" for line in lines)
            h._send(200, body, "application/x-ndjson")
        else:
            h._send(200, json.dumps(chat_single(lines)).encode())


def closed_port() -> int:
    """A local port with nothing listening on it (bound, then released)."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
