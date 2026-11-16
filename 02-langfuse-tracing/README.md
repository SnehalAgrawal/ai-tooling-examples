# Langfuse, measured

[Langfuse](https://langfuse.com) records what an LLM application does (each request as a trace of nested steps, with inputs, outputs, model, token usage and timings) and lets you score those traces, including in experiments over a dataset. This demo runs Langfuse on your own machine with Docker, instruments one small application with its Python SDK, and measures five things instead of taking them on trust:

1. **What a trace contains.** Each request is read back through the Langfuse API and compared with what the application saw: the tree of steps, the model and its parameters, token counts against Ollama's own, and latencies.
2. **What tracing costs per request.** The same requests with no Langfuse code, with tracing disabled, with tracing on but spans discarded, and with tracing on and sent to the server; with a fake model that answers instantly, so the difference is the SDK.
3. **What happens when Langfuse is down or slow.** Connection refused, a server that answers after 10 seconds, a server that answers 503, and the real server stopped mid-run: request latency, exceptions, the in-memory queue, dropped spans, logs, and how many traces arrive. Plus a short-lived script that exits in five different ways.
4. **Evaluation.** A 14-question dataset uploaded to Langfuse, an experiment over it with deterministic scores per item, and the scores read back from the API.
5. **Masking.** An email address and phone number in a request, with each of the SDK's two masking hooks, and what the stored trace contains.

It accompanies the article [Langfuse: Tracing and Evaluating an LLM Application](https://snehalagrawal.com/articles/langfuse-tracing-and-evaluating-an-llm-application/) on snehalagrawal.com.

## The application

A fleet-operations assistant for a fictional delivery company ([`fleet/`](fleet/)). It answers questions such as "Which extension does a driver call when the van breaks down?" from ten short policy and runbook snippets ([`fleet/knowledge.py`](fleet/knowledge.py)):

- **Retrieval** ([`fleet/retrieval.py`](fleet/retrieval.py)): keyword overlap, top 2 snippets. Deterministic, no model.
- **Generation** ([`fleet/llm.py`](fleet/llm.py)): Ollama's `/api/chat`, not streamed, with temperature 0, seed 42 and a 4,096-token context; or a fake model that returns the first sentence of the context instantly.
- **Tracing** ([`fleet/app.py`](fleet/app.py)): `answer_traced` wraps the same steps as `answer_plain` in three Langfuse observations: a root span `fleet-question`, a retriever `retrieve`, and a generation `generate` with the model name, its parameters, and `usage_details` from Ollama's `prompt_eval_count` and `eval_count`.

## Run it

Requirements: Docker with about 4 GB of memory free for the stack, Python 3.12 (the version tested), and [Ollama](https://ollama.com) for the parts that use a real model.

```bash
docker compose up -d                       # Langfuse on http://localhost:3000, ready in about a minute
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q              # pure logic only: no model, no server
ollama pull qwen2.5:3b                     # parts 1, 4, and the real-model variant of 2
```

Every script prints a summary, writes everything it measured to `runs/` (git-ignored) as JSON, and has `--help`. Run them from this folder:

| Part | Command | Model calls | Time (approx.) |
|---|---|---|---|
| 1. Trace contents | `.venv/bin/python -m scripts.trace_contents --model qwen2.5:3b --n 3` | 4 | under a minute |
| 2. Overhead, fake model | `.venv/bin/python -m scripts.overhead --model fake --n 500 --rounds 3` | 0 | about a minute |
| 2. Overhead, real model | `.venv/bin/python -m scripts.overhead --model qwen2.5:3b --n 20 --warmup 2 --rounds 2 --conditions none,enabled` | 88 | a few minutes |
| 3. Outages | `.venv/bin/python -m scripts.outage` | 0 | 2 to 3 minutes |
| 3. Server stopped mid-run | `.venv/bin/python -m scripts.outage --scenarios stopped --rate 20 --duration 60` | 0 | about 2 minutes |
| 3. Short-lived scripts | `.venv/bin/python -m scripts.short_lived --traces 5` | 0 | about a minute |
| 4. Evaluation | `.venv/bin/python -m scripts.evaluate --model qwen2.5:3b` | 14 | about a minute |
| 5. Masking | `.venv/bin/python -m scripts.masking` | 0 | seconds |

The UI is at <http://localhost:3000>; sign in as `demo@example.com` / `local-demo-password` (local demo values from `docker-compose.yml`). When you're done: `docker compose down` (add `-v` to delete the data), and `ollama stop qwen2.5:3b`.

### Configuration

The scripts read `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `OLLAMA_URL` and `FLEET_MODEL` from the environment ([`fleet/config.py`](fleet/config.py)). The defaults match [`docker-compose.yml`](docker-compose.yml): `http://localhost:3000`, `pk-lf-local-demo`, `sk-lf-local-demo`, `http://localhost:11434`, `qwen2.5:3b`. **Every password, secret and key in this folder is a placeholder for a stack that listens only on 127.0.0.1.** Never reuse them.

### The self-hosted stack

[`docker-compose.yml`](docker-compose.yml) is Langfuse's own file from tag `v4.72.0`, trimmed, with every image pinned by digest. Langfuse v4 is six containers: `langfuse-web` (UI and APIs) and `langfuse-worker` (processes incoming events), PostgreSQL, ClickHouse (traces, observations, scores), Redis (queue and cache), and S3-compatible storage (MinIO here; every incoming event is written there first). Only the UI/API port and MinIO's S3 port are published, and only on 127.0.0.1. Headless initialization (`LANGFUSE_INIT_*`) creates the organization, project, API keys and user at startup. Idle, after the smoke runs, the six containers used about 3.3 GB of memory (ClickHouse 1.6 GB, web 0.8 GB, worker 0.7 GB) on Docker Desktop for Mac.

### Telemetry

- **Langfuse server:** the web container sends aggregated deployment telemetry (version, counts of traces, observations, scores, datasets, up to 30 user email *domains*) to PostHog at `eu.posthog.com`, at most every 12 hours, unless `TELEMETRY_ENABLED=false` ([Langfuse docs](https://langfuse.com/self-hosting/security/telemetry)). It is set to `false` on both application containers here.
- **Image pulls:** Langfuse's compose file pulls through `docker.langfuse.com`, which counts pulls; this file pulls from Docker Hub directly (`docker.io/langfuse/...`), as the same page allows.
- **Not configurable:** when a signed-in user opens the UI, the web container asks `https://langfuse.com/api/latest-releases` for the newest version; it fails quietly offline. `LANGFUSE_DISABLE_SIGNUP_ONBOARDING=true` skips the newsletter step after sign-up.
- **Python SDK:** sends nothing anywhere except to the configured `base_url` (checked in the installed source of `langfuse` 5.1.0: no analytics client, and no URL other than documentation links and the default `https://cloud.langfuse.com`). Note that default: without `LANGFUSE_BASE_URL` or `base_url`, the SDK sends traces to Langfuse Cloud.

## How it works

- **Read back, not eyeballed.** Every check reads data through the public API: observations with `GET /api/public/v2/observations` (filtered by session ID, which the scripts set with `propagate_attributes`), scores with `GET /api/public/v3/scores`, experiment items with `GET /api/public/experiment-items`. Ingestion is asynchronous, so the scripts poll until the expected number of records is readable, with a deadline, and report how long that took.
- **One process per configuration.** The SDK keeps one client per public key in a process, so every condition (overhead), scenario (outages), exit mode (short-lived) and masking hook runs in its own Python process.
- **Overhead** ([`scripts/overhead.py`](scripts/overhead.py)): requests run back to back on one thread, each timed with `perf_counter` around the whole request; 50 untimed warm-up requests first; conditions interleaved over rounds with the order rotated. `flush()` is timed separately. For `enabled`, the script also counts the traces that arrived.
- **Outages** ([`scripts/outage.py`](scripts/outage.py)): the app sends requests at a fixed rate. The SDK's span exporter is wrapped to count spans handed to it and export results, so spans dropped because the queue was full can be counted (the SDK only logs them). The queue length is read from the OpenTelemetry batch processor after each request. These two read internal attributes of the SDK; they are measurement hooks, not something an application should do. The fake servers decode the OTLP protobuf they receive and count spans.
- **Evaluation** ([`scripts/evaluate.py`](scripts/evaluate.py)): `data/questions.json` holds 14 questions, each with the key facts a good answer states, as lists of accepted spellings (one question has no answer in the knowledge base; its "fact" is saying so). Items are upserted by a fixed ID. `run_experiment` runs the traced app per item; three item-level scores are recorded on each item's trace and one run-level score on the experiment:
  - `expected_facts_present_fraction`: the share of expected facts found in the answer;
  - `all_expected_facts_present`: 1 if every expected fact was found;
  - `source_retrieved`: 1 if retrieval returned the snippet holding the answer, so a failure can be told apart as retrieval or generation;
  - `share_all_expected_facts_present` (run level): the mean of the second.

  "Fact present" is a substring check after normalization ([`fleet/evaluate.py`](fleet/evaluate.py)). It is not correctness: an answer can contain the fact and still say something wrong.
- **Masking** ([`fleet/masking.py`](fleet/masking.py), [`scripts/masking.py`](scripts/masking.py)): one regex-based redaction (emails, phone numbers of 8 to 15 digits) in both of the SDK's shapes: `mask=` (legacy; called on each input, output and metadata value the SDK sets) and `mask_otel_spans=` (recommended by the docs for Python; called on the span attributes of each export batch).

## What you'll see

Each script prints a table and writes the details to `runs/`. Numbers from your machine will differ; the shape should not.

### Recorded results

Recorded on 2026-11-16 on an Apple M4 Pro (macOS 26.6.2, Docker Desktop), on mains power, with Langfuse 4.72.0 (self-hosted, `{"status":"OK","version":"4.72.0"}`), Python SDK `langfuse` 5.1.0 (OpenTelemetry SDK 1.45.2), Ollama 0.40.16 and `qwen2.5:3b` (`357c53fb659c…`). Before the runs, no model was loaded and no other model workload was running. Shortly after start, the six containers used about 2.5 GB of memory: web 1.06 GiB, ClickHouse 705 MiB, worker 612 MiB, MinIO 81 MiB, PostgreSQL 43 MiB, Redis 13 MiB.

- **Trace contents** (two runs): every trace read back with its three observations (a root span, a retriever, a generation). Token counts sent as `usage_details` matched Ollama's exactly (154/32, 162/25, 164/16 input/output), and Langfuse added `total`. A request sent without `usage_details` was stored with `usageDetails: {}` and no cost: Langfuse 4.72.0 inferred nothing for this model. Langfuse's root-span latency matched the app's own timing to the millisecond.
- **Overhead, fake model** (two runs × 3 rounds × 500 requests, microseconds per request at the median): no Langfuse code 64.9–65.4; `tracing_enabled=False` 92.5–92.9; tracing on, spans discarded 265.7–268.1; tracing on and sent 266.0–272.0. Tracing adds about 0.2 ms per request of three spans, and nearly all of it is creating the spans: sending them costs no more than discarding them. `flush()` after 550 traces took 97–121 ms.
- **Overhead, real model** (2 rounds × 20 requests): the difference was smaller than the variation between rounds (per-round medians 249–311 ms without tracing, 249–269 ms with it). At this scale, 0.2 ms is not measurable against a model call.
- **When Langfuse is down or slow** (1,000 requests at 50 per second, 3,000 spans per scenario):

  | Scenario | request errors | median latency | `flush()` blocked | spans exported OK | what the server received |
  |---|---|---|---|---|---|
  | up | 0 | 1.56 ms | 0.05 s | 3,000 | 1,000 of 1,000 traces |
  | connection refused | 0 | 1.55 ms | 3.34 s | 0 | nothing |
  | slow (answers after 10 s) | 0 | 1.55 ms | 13.62 s | 0 | all 3,000 spans, once each |
  | 503 responses | 0 | 1.56 ms | 3.13 s | 0 | 9,000 spans: each one three times |

  No request failed or slowed down. The cost moved to `flush()`, and to data: the slow server received every span although the SDK counted each export as failed, and with 503 responses it received each span three times, from the SDK's retries (whether a real Langfuse server removes such duplicates was not tested).
- **The real server stopped mid-run** (1,200 requests at 20 per second; `langfuse-web` stopped at 20 s, started at 40 s, healthy at 47 s): no request errors; 780 of 1,200 traces arrived. The 420 lost were exactly the requests sent between 20.2 and 41.2 seconds. Nothing is kept on disk, and failed batches are not retried later.
- **Short-lived scripts** (5 traces each): a normal exit without `flush()`, and an exit by uncaught exception, delivered 5 of 5, because the SDK flushes in an `atexit` handler. SIGTERM and `os._exit()` delivered 0 of 5.
- **Evaluation** (two runs, identical): 14 items uploaded as a dataset, one experiment per run, 43 scores read back (3 per item plus 1 per run). 13 of 14 answers contained every expected fact; q10 contained one of two. The run-level score `share_all_expected_facts_present` was 0.929, and every item's scores were read back on its own trace with the same values.
- **Masking:** with no hook, the raw email address and phone number were stored in the input and metadata of all three observations. With the legacy `mask=` hook, the inputs were masked but the email in the trace's metadata (set with `propagate_attributes`) was stored unmasked on all three observations. With `mask_otel_spans=`, the stored trace contained neither, anywhere.

### What each script prints

- `trace_contents`: per request, the tokens Ollama counted and the `usageDetails` Langfuse stored (and whether they match), the app's own timing next to the latencies Langfuse recorded for the root span and the generation, and Ollama's `total_duration`; then the tree of one trace. The last request is sent without `usage_details`, to show what Langfuse stores when the application does not report usage.
- `overhead`: microseconds per request per condition (median, p95, p99, max), per-round medians, what each condition adds over `none`, flush time, and traces arrived.
- `outage`: per scenario, request latency and errors, the queue's maximum length against its capacity, spans created / handed to the exporter / exported / dropped, export calls, how long `flush()` blocked, how long the process took to exit, the log lines the SDK wrote, and traces arrived (for `stopped`, which requests' traces were lost and when they were sent).
- `short_lived`: per exit mode, the exit code, the time from the end of the work to process exit, and traces arrived.
- `evaluate`: per item, the three scores and whether the same values were read back on the item's trace; the run-level score; the experiment ID.
- `masking`: per hook, which stored fields still contain the raw email address or phone number.

## Limitations

- **One machine.** The Langfuse server, the app and Ollama share one computer; the server is on localhost, so there is no network latency, and the server's own work competes with the app for CPU. Overhead on a real deployment, with the server elsewhere, will differ.
- **One version of each.** Langfuse server 4.72.0, Python SDK 5.1.0 (OpenTelemetry SDK 1.45.2). The SDK's batching is OpenTelemetry's `BatchSpanProcessor`; its defaults come from that package and may change with it. The JS/TS SDK was not tested.
- **Simulated failures.** Connection refused, a slow server and 503 responses come from local stand-ins, not from a real Langfuse under load. Only `stopped` uses the real server.
- **A small, simple app.** Keyword retrieval over ten snippets, one generation per request, 14 dataset items, a 3-billion-parameter model. The demo is about what Langfuse records and costs, not about how good the assistant is.
- **Masking by regex.** The patterns catch the formats in this demo; real PII detection needs more than two regular expressions (an 8-digit date such as 2026-11-16 would be masked as a phone number).
- **Internal attributes.** The queue length and the dropped-span count read private attributes of the SDK and OpenTelemetry; a future version may rename them.
