"""Does LangChain open any connection you did not ask for? Every socket the process opens is recorded.

A Python audit hook (``sys.addaudithook``) sees every ``socket.connect`` and ``socket.getaddrinfo``
in this process. The script imports langchain, langchain_core, langchain_ollama, and langgraph,
then makes a chat call, a streamed call, and a structured-output call against the local stub, and
prints every destination. The hook also BLOCKS any connection to a non-loopback address, so this
script cannot send anything off the machine, whatever the environment says.

With ``--tracing-on`` it sets LANGSMITH_TRACING=true for this process only, with LANGSMITH_ENDPOINT
pointed at a local recorder and a placeholder API key, to show what LangSmith tracing would upload.

    python network_audit.py
    python network_audit.py --tracing-on
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler

EVENTS: list[dict] = []


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def audit(event: str, args: tuple) -> None:
    if event == "socket.connect":
        addr = args[1]
        if isinstance(addr, tuple):
            host, port = addr[0], addr[1]
            EVENTS.append({"event": "connect", "host": host, "port": port})
            if not _is_loopback(str(host)):
                raise RuntimeError(f"network_audit: blocked a connection to {host}:{port}")
        else:  # a Unix socket path
            EVENTS.append({"event": "connect", "path": str(addr)})
    elif event == "socket.getaddrinfo":
        host, port = args[0], args[1]
        EVENTS.append({"event": "getaddrinfo", "host": host if isinstance(host, str) else repr(host), "port": port})
        if (
            isinstance(host, (str, bytes))
            and host
            and not _is_loopback(host.decode() if isinstance(host, bytes) else host)
        ):
            raise RuntimeError(f"network_audit: blocked a DNS lookup of {host!r}")


class Recorder:
    """A local catch-all HTTP server standing in for the LangSmith API."""

    def __init__(self) -> None:
        from wire import _Server

        self.requests: list[dict] = []
        rec = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                enc = (self.headers.get("Content-Encoding") or "").lower()
                if enc == "zstd":
                    import zstandard

                    body = zstandard.ZstdDecompressor().decompressobj().decompress(body)
                rec.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "content_type": self.headers.get("Content-Type"),
                        "content_encoding": enc or None,
                        "bytes": len(body),
                        "body": body,
                    }
                )
                payload = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PATCH = do_PUT = _any

        self.server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracing-on", action="store_true")
    args = ap.parse_args()

    sys.addaudithook(audit)
    recorder = None
    if args.tracing_on:
        recorder = Recorder()
        os.environ.update(
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_ENDPOINT": recorder.url,
                "LANGSMITH_API_KEY": "placeholder-not-a-key",
                "LANGSMITH_PROJECT": "network-audit",
            }
        )
    else:
        from common import refuse_if_tracing

        refuse_if_tracing()

    import langchain  # noqa: F401
    import langchain_core  # noqa: F401
    import langgraph  # noqa: F401
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama

    import fleet
    from wire import StubOllama

    after_imports = len(EVENTS)
    print(f"Imports of langchain, langchain_core, langchain_ollama, langgraph: {after_imports} socket events")

    with StubOllama(content=json.dumps({"package_id": "PKG-10432", "city": "Leeds", "reason": "weather"})) as stub:
        stub_port = stub.port
        llm = ChatOllama(model="stub-model", base_url=stub.url)
        msgs = [SystemMessage(fleet.SYSTEM_PROMPT), HumanMessage(fleet.QUESTION)]
        llm.invoke(msgs)
        list(llm.stream(msgs))
        llm.with_structured_output(fleet.DeliveryException).invoke(msgs)
        if args.tracing_on:
            from langchain_core.tracers.langchain import wait_for_all_tracers

            wait_for_all_tracers()

    recorder_port = recorder.server.server_address[1] if recorder else None
    by_dest: dict[str, int] = {}
    for e in EVENTS:
        if e["event"] != "connect" or "host" not in e:
            continue
        port = e["port"]
        who = "stub (/api/chat)" if port == stub_port else "LangSmith recorder" if port == recorder_port else "other"
        key = f"{e['host']}:{port} [{who}]"
        by_dest[key] = by_dest.get(key, 0) + 1
    non_loopback = [e for e in EVENTS if e.get("host") and not _is_loopback(str(e["host"]))]
    print(f"Socket events in total: {len(EVENTS)}; destinations: {by_dest or 'none'}")
    print(f"Attempts to reach a non-loopback address: {len(non_loopback)} {non_loopback if non_loopback else ''}")

    if recorder:
        print(f"\nWith tracing on, the LangSmith client sent {len(recorder.requests)} request(s) to the recorder:")
        needles = {
            "the system prompt": fleet.SYSTEM_PROMPT[:40],
            "the question": fleet.QUESTION,
            "the model reply (stub)": "Leeds",
        }
        for r in recorder.requests:
            found = [name for name, text in needles.items() if text.encode() in r["body"]]
            print(
                f"  {r['method']} {r['path']}  {r['bytes']} bytes ({r['content_type']}, "
                f"encoding {r['content_encoding']}); contains: {', '.join(found) or 'none of the checked texts'}"
            )


if __name__ == "__main__":
    main()
