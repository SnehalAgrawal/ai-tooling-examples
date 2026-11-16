"""A small Ollama client (standard library only) and a helper that starts a dedicated Ollama server.

Every experiment in this demo runs against its own server on a separate port, started with known settings, so
requests from other programs on the machine can't share its model, its queue, or its prompt cache.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path


class Ollama:
    def __init__(self, base_url: str = "http://127.0.0.1:11435", timeout: float = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e

    def version(self) -> str:
        return self._request("GET", "/api/version")["version"]

    def tags(self) -> list[dict]:
        return self._request("GET", "/api/tags")["models"]

    def show(self, model: str) -> dict:
        return self._request("POST", "/api/show", {"model": model})

    def ps(self) -> list[dict]:
        return self._request("GET", "/api/ps")["models"]

    def unload(self, model: str) -> None:
        """Unload a model: a request with keep_alive 0 and no messages."""
        self._request("POST", "/api/chat", {"model": model, "messages": [], "keep_alive": 0})
        for _ in range(100):  # wait until the server reports it gone
            if all(m["name"] != model for m in self.ps()):
                return
            time.sleep(0.1)
        raise RuntimeError(f"{model} still loaded after unload")

    def chat(self, model: str, messages: list[dict], options: dict | None = None, keep_alive=None) -> dict:
        """Native /api/chat, not streamed. Returns the full response (text, token counts, timings)."""
        body = {"model": model, "messages": messages, "stream": False, "options": options or {}}
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        start = time.perf_counter()
        response = self._request("POST", "/api/chat", body)
        response["wall_seconds"] = time.perf_counter() - start
        response["text"] = response["message"]["content"]
        return response

    def chat_openai(self, model: str, messages: list[dict], **fields) -> dict:
        """OpenAI-compatible /v1/chat/completions. It has no `options`: num_ctx can't be set here."""
        start = time.perf_counter()
        response = self._request("POST", "/v1/chat/completions", {"model": model, "messages": messages, **fields})
        response["wall_seconds"] = time.perf_counter() - start
        response["text"] = response["choices"][0]["message"]["content"]
        return response


def ollama_binary() -> str:
    found = shutil.which("ollama")
    if not found:
        raise SystemExit("The ollama command was not found. Install Ollama from https://ollama.com")
    return found


@contextmanager
def dedicated_server(port: int = 11435, log_path: Path | None = None, **settings: str):
    """Start `ollama serve` on its own port with the given OLLAMA_* settings, and stop it afterwards.

    It uses the same model files as your usual server (no download). OLLAMA_NOPRUNE keeps it from pruning
    model files on start-up, and OLLAMA_NO_CLOUD turns off cloud features.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("OLLAMA_")}
    env.update({"OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_NOPRUNE": "1", "OLLAMA_NO_CLOUD": "1"})
    env.update({k: str(v) for k, v in settings.items()})
    log = open(log_path, "w") if log_path else subprocess.DEVNULL
    process = subprocess.Popen([ollama_binary(), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT)
    client = Ollama(f"http://127.0.0.1:{port}")
    try:
        for _ in range(200):
            try:
                client.version()
                break
            except (urllib.error.URLError, ConnectionError, RuntimeError):
                if process.poll() is not None:
                    raise SystemExit(f"ollama serve exited early (port {port} in use?)") from None
                time.sleep(0.1)
        else:
            raise SystemExit("ollama serve did not start within 20 seconds")
        yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        if log_path:
            log.close()
