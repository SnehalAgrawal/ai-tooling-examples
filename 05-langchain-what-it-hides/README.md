# LangChain: what it gives you and what it hides

[LangChain](https://docs.langchain.com/oss/python/langchain/overview) wraps a model call in a common interface: one `ChatOllama` (or `ChatOpenAI`, ...) object with `invoke`, `stream`, `bind_tools`, and `with_structured_output`. This demo looks at what that interface does underneath, by putting a small logging proxy between LangChain and Ollama and comparing every request and response with the same call written by hand with `httpx`. No API keys, no hosted service: everything runs against a local Ollama, or against a local stub that answers like Ollama.

It measures five things, with one example throughout (a fleet-operations assistant: delayed packages, delivery exceptions):

1. **What a call sends.** The request body LangChain puts on the wire for a chat call, sampling parameters, tools, a tool result, and each `with_structured_output` method, diffed against the hand-written request.
2. **Structured output.** Fifteen delivery-exception messages extracted into a Pydantic model with each method, on three small models, graded field by field; and, against the stub, what each method raises or returns when the reply is wrong.
3. **Overhead.** Time per call with the model taken out (an instant stub), import time, and the installed dependency footprint.
4. **Retries and errors.** Connection refused, HTTP 500 and 404, a slow reply, a malformed body, and an error inside the stream: the exception, the number of requests, the time taken.
5. **Streaming and token counts.** Whether `.stream()` yields what came over the wire, and where Ollama's token counts end up.

It accompanies the article [LangChain: What It Gives You and What It Hides](https://snehalagrawal.com/articles/langchain-what-it-gives-you-and-what-it-hides/) on snehalagrawal.com.

## Run it

Requirements: Python 3.11 or newer (recorded with 3.12.13), and [Ollama](https://ollama.com) for the measurements that use a model.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ollama pull qwen2.5:3b && ollama pull llama3.2:3b && ollama pull llama3.2:1b

.venv/bin/python -m pytest -q             # 22 tests, no model, under 10 seconds
.venv/bin/python network_audit.py         # what the process connects to, no model, seconds

.venv/bin/python sends.py                 # 1. what a call sends: qwen2.5:3b, 23 requests
.venv/bin/python structured.py            # 2. structured output: 3 models x 4 methods x 15 = 180 requests
.venv/bin/python overhead.py              # 3. overhead: stub only, N=500, about a minute
.venv/bin/python errors.py                # 4. errors and retries: stub only, about 50 seconds
.venv/bin/python streaming.py             # 5. streaming: qwen2.5:3b, 3 requests
```

Each script writes `runs/<name>-<UTC time>/results.json` (git-ignored); the scripts that use the proxy also write `wire.jsonl`, every request and response line as it crossed the wire. `--out DIR` chooses the folder. `sends.py` and `streaming.py` take `--stub` to run without a model (the request bodies are the same), and `--models` to run more than one model. `structured.py --limit N` runs only the first N messages, and `--failure-modes-only` only the stub part.

Every model request is sent with `temperature 0`, `seed 42`, and `num_ctx 8192` (`common.SAMPLING`), except where a case is about the defaults. `structured.py` unloads the other demo models before each model and that model afterwards; `sends.py` and `streaming.py` unload the models they used at the end.

The pinned versions are in [`requirements.txt`](requirements.txt), compiled from [`requirements.in`](requirements.in) with every package released on or before 2026-11-02:

```bash
uv pip compile requirements.in --python-version 3.12 --exclude-newer 2026-11-03T00:00:00Z -o requirements.txt
```

| Package | Version | Released |
|---|---|---|
| `langchain` | 1.4.8 | 2026-11-02 |
| `langchain-core` | 1.7.4 | 2026-11-02 |
| `langchain-ollama` | 1.1.0 | 2026-04-07 |
| `ollama` (Python client, pulled in by `langchain-ollama`) | 0.6.3 | 2026-09-29 |
| `langgraph` (pulled in by `langchain`) | 1.2.19 | 2026-10-30 |
| `langsmith` (pulled in by `langchain-core`) | 0.14.17 | 2026-11-02 |
| `httpx` | 0.28.1 | 2024-12-06 |
| `pydantic` | 2.14.2 | 2026-10-29 |

### Nothing leaves the machine

LangSmith tracing is off unless `LANGSMITH_TRACING` (or `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING_V2`, `LANGCHAIN_TRACING`) is `"true"` (`langsmith/utils.py`, `tracing_is_enabled`). Every script refuses to start if one of them is set. [`network_audit.py`](network_audit.py) records every socket the process opens with a Python audit hook, and blocks any connection to a non-loopback address: importing `langchain`, `langchain_core`, `langchain_ollama`, and `langgraph` opened no socket, and the calls opened one, to the stub.

`network_audit.py --tracing-on` sets `LANGSMITH_TRACING=true` for its own process only, with `LANGSMITH_ENDPOINT` pointed at a local recorder and a placeholder key, to show what tracing would upload: a `GET /info`, then a `POST /runs/multipart` containing the system prompt, the question, and the model's reply.

## How it works

- **The wire** ([`wire.py`](wire.py)): `LoggingProxy` forwards each request unchanged to Ollama (or the stub), streams the response back line by line, and records the request body, headers, and every NDJSON response line. `StubOllama` answers `/api/chat` instantly with a canned reply (streamed unless the request says `"stream": false`, as Ollama does), counts requests, and has failure modes. Standard library only, so the measuring tools add no dependency.
- **The example** ([`fleet.py`](fleet.py)): a fixed fleet status, two tools (`get_package_status`, `list_delayed_packages`), the `DeliveryException` model (`package_id`, `city`, `reason` from seven values, `delay_hours` as an optional integer), and fifteen messages with the expected values. Some test units ("two days", "1.5 days", "half a day"), a distractor ID (x10), lowercase input (x13), a missing delay (x07, x12), and a city that is not the first one named (x15).
- **The analysis** ([`analysis.py`](analysis.py)): the JSON diff (numbers compare by value, so `0` and `0.0` are equal; `true` and `1` are not), the field-by-field grader (IDs and cities ignore case and surrounding spaces; `delay_hours` accepts `36.0` or `"36"` for 36), and the percentile.
- **The baseline** ([`common.py`](common.py) `plain_chat`): one `httpx` POST to `/api/chat` with exactly the body written in the script.
- **The tests** ([`tests/`](tests/)): the diff and the grader, the stub and the proxy, and the wire behaviours listed below, pinned against the stub, so a LangChain or ollama release that changes them fails a test.

## What you'll see

Recorded on 2026-11-16 on an Apple M4 Pro (macOS 26.6.2), on mains power, with Ollama 0.40.16 and `qwen2.5:3b` (`357c53fb659c…`), `llama3.2:3b` (`a80c4f17acd5…`), and `llama3.2:1b` (`baf6a787fdff…`). Model runs waited until no other model workload had been active for 60 seconds. `structured.py` ran twice: all 180 replies were identical, word for word, between the runs. `overhead.py` ran twice, with no model loaded; the medians agreed within 3%.

### Structured output, measured (`structured.py`)

Fields correct out of 60 (15 messages × 4 fields); `plain_json_schema` is the hand-written request with the same schema in `format`:

| Model | `json_schema` (default) | `function_calling` | `json_mode` | `plain_json_schema` |
|---|---|---|---|---|
| `qwen2.5:3b` | 57 | 57 | 57 | 57 |
| `llama3.2:3b` | 56 | 57 | 53 (1 `OutputParserException`) | 56 |
| `llama3.2:1b` | 47 | **20** | 42 (3 `OutputParserException`) | 47 |

- **LangChain's default and the hand-written request gave identical results** for every model: they send the same request.
- **`llama3.2:1b` with `function_calling`:** 6 of 15 returned `None` with no error (the model wrote the tool definition, or a call, as text instead of making a tool call); 3 raised `ValidationError` (two left out the required `package_id`, one sent `1.5` for the integer `delay_hours`); 6 parsed.
- **`llama3.2:1b` with `json_schema`:** every reply parsed. 8 of its 13 missed fields were `delay_hours`: empty seven times, even for "5 hours" or "12h", and 2 instead of 48 for "two days". The field is optional, so an empty value passes the schema.

### Overhead (`overhead.py`, stub only)

| Call | Median per call (500 calls) |
|---|---|
| plain `httpx` POST | 0.29 ms |
| `ollama` client | 0.31 ms |
| LangChain `invoke()` | 0.57 ms |
| LangChain `.stream()` | 0.52 ms |
| hand-written structured request + Pydantic | 0.30 ms |
| LangChain `with_structured_output()` | 0.95 ms |

Import time (fresh process, median of 10): `httpx` 38 ms, `ollama` 103 ms, `langchain_core` 251 ms, `langchain_ollama` 318 ms; the largest single part is `langsmith`, about 100 ms, imported even with tracing off. Installed footprint: `httpx` 7 packages / 3.9 MB, `ollama` 12 / 13.1 MB, `langchain-ollama` 35 / 38.9 MB, `langchain` (without `langchain-ollama`) 39 / 45.3 MB.

The results below don't depend on a model; they come from the stub runs and the tests.

### What a call sends (from `sends.py --stub`, the same with a model)

| Call | LangChain's request, against the hand-written one |
|---|---|
| `invoke()` with defaults | `"stream": true` (LangChain streams every call, `invoke()` included, and joins the chunks), `"options": {}`, `"tools": []`. No `keep_alive`, `think`, or `format`: unset fields are left out, so Ollama's own defaults apply. |
| `temperature=0, seed=42, num_ctx=8192` on the constructor | All three reach `options`. |
| the same, plus `invoke(..., options={"num_predict": 64})` | `options` is **replaced**, not merged: `{"num_predict": 64}` only; temperature, seed, and num_ctx are gone. |
| `ChatOllama(max_tokens=64, timeout=5, max_retries=2)` | Accepted without a warning and ignored: none is a `ChatOllama` field, and LangChain models ignore unknown arguments. Nothing reaches the wire. |
| `invoke(..., temperature=0)` | `TypeError: Client.chat() got an unexpected keyword argument 'temperature'`, before any request. |
| `bind_tools()` with two functions | The same tools, except a parameter's `default` is dropped. |
| `bind_tools(..., tool_choice=...)` | `tool_choice` is not sent (the docstring says so: Ollama does not support it). |
| a tool result sent back | The tool call's `id` and the `ToolMessage`'s `tool_call_id` are not sent, and neither is `tool_name`, which Ollama's API uses to say which tool a result belongs to. |
| `with_structured_output()` (default) and `method="json_schema"` | The same request: the Pydantic JSON schema as `format`. |
| `method="json_mode"` | `"format": "json"`; the schema is only in the prompt you write. |
| `method="function_calling"` | A tool named after the model. Its `delay_hours` parameter (`int \| None`) arrives with only a description: its `anyOf` type and its `default` are dropped. |

The dropped schema fields are not LangChain's choice: `convert_to_openai_tool` generates them, and the `ollama` Python client that `langchain-ollama` uses validates tools into a model whose properties keep only `type`, `items`, `description`, and `enum` (`ollama/_types.py`, `Tool.Function.Parameters.Property`). `sends.py` prints both diffs: LangChain against the hand-written request, and LangChain's generated tools against what reached the wire.

Ollama 0.40.16 returns an `id` with each tool call (`call_...`); LangChain replaces it with its own UUID, and the `ollama` client does not send IDs back.

### What structured output does when the reply is wrong (`structured.py`, stub part)

| Reply | `json_schema` / `json_mode` | `function_calling` |
|---|---|---|
| Prose, or JSON in the text instead of a tool call | raises `OutputParserException` | **returns `None`, no error** |
| JSON with a value outside the enum, or a required field missing | raises `OutputParserException` | (as a tool call) raises `pydantic.ValidationError` |
| Valid JSON inside a Markdown code fence | parsed: the fence is stripped | returns `None` |
| A tool call to an unknown tool name | | raises `OutputParserException` |

With `include_raw=True` nothing is raised: the error is returned in `parsing_error`, and `parsed` is `None`. In every case the stub received exactly one request: there is no automatic retry.

### Errors and retries (`errors.py`, stub only)

- `ChatOllama` has **no timeout** by default: the `ollama` client passes `timeout=None` to httpx. A reply that took 7 seconds was waited for; plain httpx gave up at its 5-second default. `client_kwargs={"timeout": 1.0}` sets one (`httpx.ReadTimeout`).
- **No retry** by default, and `max_retries` is ignored (above). `.with_retry()` makes 3 attempts with exponential backoff and jitter, 3 to 5 seconds in all, and by default retries **every** exception, including a 404 "model not found" and a malformed body.
- Exceptions come from three libraries: `httpx.ConnectError` for a refused connection (the `ollama` client converts it to its own `ConnectionError` only for non-streamed requests, and LangChain always streams), `ollama.ResponseError` for HTTP errors and for an error line inside the stream, and `json.JSONDecodeError` for a malformed body.

### Streaming and token counts (`streaming.py`)

- `.stream()` yields one chunk per line on the wire, plus a final empty chunk that `langchain-core` adds (`chunk_position="last"`). The joined text is the text on the wire.
- Ollama's `prompt_eval_count` and `eval_count` become `usage_metadata` (`input_tokens`, `output_tokens`, `total_tokens`) on the chunk built from Ollama's final line, which is not the last chunk; adding the chunks together gives the totals. `invoke()` returns them on the message.
- `response_metadata` carries Ollama's final line, minus fields the `ollama` client does not know: Ollama 0.40.16 also sends `prompt_eval_cached_count`, which does not reach LangChain.
- `invoke()` streams on the wire too, and fires `on_llm_new_token` once per line.

## Limitations

- **One integration.** This is `ChatOllama`. Other integrations (`ChatOpenAI`, `ChatAnthropic`, ...) have their own defaults for retries, timeouts, and structured output; none was tested.
- **Small local models.** Three models of 1 to 3 billion parameters; how a larger or hosted model would perform was not tested.
- **The stub is not Ollama.** Its replies follow the shape of Ollama's `/api/chat`, and its failure modes are chosen ones. The overhead measured against it is the client's, on loopback, with no model time.
- **Versions move fast.** Every finding above is for the pinned versions; the tests pin the wire behaviours so that a change shows.
