"""Record what a run depends on, and what Ollama actually allocates when you don't say.

For each model: the digest behind the tag, quantization, the parameters and chat template baked into the model,
and the context length the server allocates (read back from /api/ps) with no num_ctx, with num_ctx 8192, and
through the OpenAI-compatible endpoint, which can't set num_ctx. Then a long request through both endpoints:
how many prompt tokens did the model actually read?

    python scripts/inspect_setup.py                  # a few minutes; writes runs/setup.json

Runs on a dedicated server (port 11435) started with default settings.
"""

import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ollama_client import dedicated_server  # noqa: E402
from prompts import SYSTEM  # noqa: E402

RUNS = Path(__file__).resolve().parents[1] / "runs"
MODELS = ["qwen2.5:3b", "llama3.2:3b"]


def machine():
    def sysctl(name):
        try:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()
        except FileNotFoundError:
            return None

    power = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout.splitlines()[:1] \
        if platform.system() == "Darwin" else []
    memory = sysctl("hw.memsize")
    return {"platform": platform.platform(), "cpu": sysctl("machdep.cpu.brand_string"),
            "memory_gib": round(int(memory) / 2**30, 1) if memory else None, "power": power}


def loaded_context(client, model):
    return [m.get("context_length") for m in client.ps() if m["name"] == model]


def long_request(words=7000):
    """A request longer than 4,096 tokens: a delivery log, then a question about its first line."""
    lines = [f"Scan {i:05d}: package {1000 + i % 900} scanned at hub {['Pune', 'Delhi', 'Chennai'][i % 3]}."
             for i in range(words // 9)]
    lines[0] = "Scan 00000: package 4711 was handed to the driver Asha at the Pune hub."
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Delivery log:\n" + "\n".join(lines) +
             "\n\nQuestion: which driver received package 4711? Answer with the name only."}]


def main():
    RUNS.mkdir(exist_ok=True)
    result = {"recorded_utc": datetime.now(UTC).isoformat(timespec="seconds"), "machine": machine()}
    with dedicated_server(11435, log_path=RUNS / "server-inspect.log") as client:
        result["ollama_version"] = client.version()
        tags = {m["name"]: m for m in client.tags()}
        result["models"] = {}
        for model in MODELS:
            show = client.show(model)
            info = show.get("model_info", {})
            trained = next((v for k, v in info.items() if k.endswith(".context_length")), None)
            entry = {
                "digest": tags[model]["digest"],
                "id": tags[model]["digest"][:12],
                "details": show.get("details"),
                "trained_context_length": trained,
                "parameters_in_model": show.get("parameters", ""),
                "template_sha256": hashlib.sha256(show.get("template", "").encode()).hexdigest()[:16],
                "capabilities": show.get("capabilities"),
            }
            say = [{"role": "user", "content": "Say OK."}]
            if loaded_context(client, model):
                client.unload(model)
            client.chat(model, say, {"num_predict": 1})
            entry["context_no_num_ctx"] = loaded_context(client, model)
            client.unload(model)
            client.chat(model, say, {"num_predict": 1, "num_ctx": 8192})
            entry["context_num_ctx_8192"] = loaded_context(client, model)
            client.unload(model)
            client.chat_openai(model, say, max_tokens=1)
            entry["context_openai_endpoint"] = loaded_context(client, model)
            client.unload(model)

            long = long_request()
            native = client.chat(model, long, {"temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 20})
            client.unload(model)
            openai = client.chat_openai(model, long, temperature=0, seed=42, max_tokens=20)
            client.unload(model)
            entry["long_request"] = {
                "characters": sum(len(m["content"]) for m in long),
                "native_num_ctx_16384": {"prompt_tokens": native.get("prompt_eval_count"), "reply": native["text"]},
                "openai_endpoint": {"prompt_tokens": openai.get("usage", {}).get("prompt_tokens"),
                                    "reply": openai["text"]},
            }
            result["models"][model] = entry
            print(json.dumps({model: entry}, indent=2), flush=True)
    # How the server chose its default context, from its own log (Ollama 0.40.x logs "vram-based default context").
    for line in (RUNS / "server-inspect.log").read_text(errors="replace").splitlines():
        if "vram-based default context" in line:
            result["server_default_context_log"] = line.split("msg=", 1)[-1]
            break
    (RUNS / "setup.json").write_text(json.dumps(result, indent=2))
    print(f"\nOllama {result['ollama_version']}, {result['machine']}")


if __name__ == "__main__":
    main()
