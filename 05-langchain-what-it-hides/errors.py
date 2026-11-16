"""Measurement 4: what happens when the server fails. No model: every case uses the stub or a closed port.

For each failure (connection refused, HTTP 500, HTTP 404, a slow reply, a malformed body, an error
line inside the stream), four clients are tried:

- ``langchain``: ChatOllama with its defaults
- ``langchain_max_retries``: ChatOllama(max_retries=2), a name other chat models accept
- ``langchain_with_retry``: ChatOllama(...).with_retry() with its defaults
- ``plain_httpx``: one httpx POST with httpx's defaults

and the exception, the time taken, and the number of requests the server received are recorded.
Attempts are also counted with a callback (on_chat_model_start), which works for the closed port too.

    python errors.py                    # about 40 seconds: the slow case waits past httpx's 5 s default
    python errors.py --slow-seconds 2   # quicker; then no client times out
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_ollama import ChatOllama

from common import refuse_if_tracing, run_dir, write_json
from wire import StubOllama, closed_port


class AttemptCounter(BaseCallbackHandler):
    def __init__(self) -> None:
        self.starts = 0
        self.errors = 0

    def on_chat_model_start(self, *args, **kwargs) -> None:
        self.starts += 1

    def on_llm_error(self, *args, **kwargs) -> None:
        self.errors += 1


def describe(e: BaseException) -> str:
    first = str(e).splitlines()[0] if str(e) else ""
    return f"{type(e).__module__}.{type(e).__name__}: {first[:150]}"


def clients(url: str) -> dict[str, Any]:
    def lc(**kw):
        return lambda counter: ChatOllama(model="stub-model", base_url=url, **kw).invoke(
            "Which packages are delayed?", config={"callbacks": [counter]}
        )

    def lc_retry(counter):
        return (
            ChatOllama(model="stub-model", base_url=url)
            .with_retry()
            .invoke("Which packages are delayed?", config={"callbacks": [counter]})
        )

    def lc_timeout(counter):
        return ChatOllama(model="stub-model", base_url=url, client_kwargs={"timeout": 1.0}).invoke(
            "Which packages are delayed?", config={"callbacks": [counter]}
        )

    def plain(counter):
        counter.starts += 1
        r = httpx.post(
            f"{url}/api/chat",
            json={"model": "stub-model", "messages": [{"role": "user", "content": "x"}], "stream": False},
        )
        r.raise_for_status()
        return r.json()

    return {
        "langchain": lc(),
        "langchain_max_retries": lc(max_retries=2),
        "langchain_with_retry": lc_retry,
        "langchain_client_timeout_1s": lc_timeout,
        "plain_httpx": plain,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--slow-seconds", type=float, default=7.0, help="delay of the slow reply (httpx's default timeout is 5 s)"
    )
    ap.add_argument("--out")
    args = ap.parse_args()
    refuse_if_tracing()
    out = run_dir("errors", args.out)

    rows = []
    with StubOllama() as stub:
        stub.delay_s = args.slow_seconds
        scenarios = [
            ("connection_refused", None),
            ("http500", "http500"),
            ("http404_model_not_found", "http404"),
            ("slow_reply", "slow"),
            ("malformed_body", "malformed"),
            ("error_line_in_stream", "stream_error"),
        ]
        for scenario, mode in scenarios:
            url = f"http://127.0.0.1:{closed_port()}" if mode is None else stub.url
            for name, call in clients(url).items():
                if name == "langchain_client_timeout_1s" and scenario != "slow_reply":
                    continue
                stub.reset(mode or "ok")
                counter = AttemptCounter()
                t0 = time.perf_counter()
                try:
                    call(counter)
                    outcome = "returned normally"
                except Exception as e:
                    outcome = describe(e)
                elapsed = time.perf_counter() - t0
                row = {
                    "scenario": scenario,
                    "client": name,
                    "outcome": outcome,
                    "elapsed_s": round(elapsed, 2),
                    "server_requests": None if mode is None else stub.requests,
                    "attempts_seen_by_callbacks": counter.starts,
                }
                rows.append(row)
                reqs = "-" if row["server_requests"] is None else row["server_requests"]
                print(
                    f"{scenario:24} {name:28} requests={reqs!s:2} attempts={counter.starts}  "
                    f"{row['elapsed_s']:6.2f}s  {outcome}"
                )
    write_json(out / "results.json", {"slow_seconds": args.slow_seconds, "rows": rows})
    print(f"\nSaved {out / 'results.json'}")


if __name__ == "__main__":
    main()
