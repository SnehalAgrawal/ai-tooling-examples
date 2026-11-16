"""Two chat models with the same interface: Ollama over its HTTP API, and an instant fake.

`chat(messages)` returns a ModelReply. Token counts come from the model server (Ollama's
`prompt_eval_count` and `eval_count`); the fake invents them from word counts.
"""

from dataclasses import dataclass, field

import httpx

from fleet.config import OLLAMA_URL

# Sent with every Ollama request: deterministic decoding and a fixed context size.
OLLAMA_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096}


@dataclass
class ModelReply:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    raw: dict = field(default_factory=dict)  # Ollama's response minus the message, for timings


class OllamaChat:
    def __init__(self, model: str, base_url: str = OLLAMA_URL, timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self.parameters = dict(OLLAMA_OPTIONS)

    def chat(self, messages: list[dict[str, str]]) -> ModelReply:
        r = self._client.post(
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": messages, "stream": False, "options": self.parameters},
        )
        r.raise_for_status()
        body = r.json()
        raw = {k: v for k, v in body.items() if k != "message"}
        return ModelReply(
            content=body["message"]["content"],
            model=body.get("model", self.model),
            input_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
            raw=raw,
        )

    def digest(self) -> str | None:
        """The model's digest as Ollama reports it (/api/tags), to record the exact weights used."""
        r = self._client.get(f"{self.base_url}/api/tags")
        r.raise_for_status()
        for m in r.json().get("models", []):
            if m.get("name") == self.model or m.get("model") == self.model:
                return m.get("digest")
        return None

    def server_version(self) -> str | None:
        r = self._client.get(f"{self.base_url}/api/version")
        r.raise_for_status()
        return r.json().get("version")


class FakeChat:
    """Answers instantly with the first sentence of the context, so timings measure only the code around it."""

    model = "fake-model"
    parameters: dict = {}

    def chat(self, messages: list[dict[str, str]]) -> ModelReply:
        user = messages[-1]["content"]
        context = user.split("Context:\n", 1)[-1]
        first_line = next((ln for ln in context.splitlines() if ln.strip()), "I don't know.")
        answer = first_line.split("] ", 1)[-1].split(". ")[0].rstrip(".") + "."
        prompt_words = sum(len(m["content"].split()) for m in messages)
        return ModelReply(
            content=answer,
            model=self.model,
            input_tokens=prompt_words,
            output_tokens=len(answer.split()),
        )


def make_model(name: str):
    return FakeChat() if name == "fake" else OllamaChat(name)
