# Ollama, reproducibly

[Ollama](https://ollama.com) makes running a local model one command. Running it *reproducibly*, so the same request gives the same reply tomorrow and on a colleague's machine, depends on settings you may never have set. This demo measures which ones matter:

1. **What a run depends on.** The model's digest behind its tag, its quantization and chat template, and the context length the server actually allocates when you don't set one, read back from the server.
2. **The context window you didn't choose.** The same 14,835-token request through the native API with `num_ctx` set, and through the OpenAI-compatible endpoint, which can't set it.
3. **Which settings change the reply.** Eight requests at temperature 0 with a fixed seed, compared word for word across reloads, warm models, the prompt cache, context and batch sizes, CPU against GPU, the OpenAI-compatible endpoint, sampling seeds, and concurrent requests.

It accompanies the article [Ollama for Development: Running Local Models Reproducibly](https://snehalagrawal.com/articles/ollama-for-development-running-local-models-reproducibly/) on snehalagrawal.com.

## Run it

Requirements: Python 3.11 or newer (standard library only: nothing to install) and [Ollama](https://ollama.com).

```bash
ollama pull qwen2.5:3b && ollama pull llama3.2:3b
python3 scripts/inspect_setup.py                    # part 1 and 2: a few minutes; runs/setup.json
python3 scripts/determinism.py                      # part 3: both models, about 25 minutes
python3 scripts/determinism.py --tag run2           # again, to check the comparison itself repeats
python3 -m unittest discover -s tests               # the comparison logic, no model
```

**Every experiment runs on its own Ollama server.** The scripts start `ollama serve` on port 11435 (and 11436 for the concurrency test) with known settings, and stop it afterwards. Your usual server on 11434 isn't used, so other programs can't share the model, its queue, or its prompt cache during a run. The dedicated server uses the same model files (no download); it starts with `OLLAMA_NOPRUNE=1` so it never prunes model files, and `OLLAMA_NO_CLOUD=1`. Server logs are saved in `runs/`.

## How it works

- [`ollama_client.py`](ollama_client.py): a small client for the native API (`/api/chat`, `/api/show`, `/api/ps`, `/api/tags`) and the OpenAI-compatible `/v1/chat/completions`, plus `dedicated_server()`, which starts and stops a server with given `OLLAMA_*` settings. Unloading uses a request with `keep_alive: 0` and then waits until `/api/ps` no longer lists the model.
- [`prompts.py`](prompts.py): one fleet-operations assistant (a system prompt) and eight writing tasks: a customer update, a delay summary, an explanation for a new dispatcher, and so on. Replies are a paragraph or two, capped at 256 tokens, so small differences have room to show.
- [`scripts/inspect_setup.py`](scripts/inspect_setup.py): records the digest, details, baked-in parameters, a hash of the chat template, the trained context length, and the context length `/api/ps` reports after loading the model three ways. Then it sends a request longer than 4,096 tokens (a delivery log whose first line names the driver who received package 4711) through both endpoints and records how many prompt tokens the model read and what it answered.
- [`scripts/determinism.py`](scripts/determinism.py): the conditions below, each compared reply by reply with a **reference run**: temperature 0, seed 42, `num_ctx` 8192, the model unloaded before every request.
- [`compare.py`](compare.py): identical text or not, and the word where a reply first differs.

| Condition | What changes from the reference |
|---|---|
| `cold_again` | nothing: the reference procedure, run again |
| `warm_in_order` | the model stays loaded between requests |
| `warm_twice` | the model stays loaded, and each request is sent twice in a row |
| `num_ctx_4096`, `num_ctx_16384` | the context length (every prompt fits in all three) |
| `num_batch_64` | the prompt batch size |
| `cpu_only` | `num_gpu: 0`, no GPU layers |
| `openai_endpoint` | `/v1/chat/completions` with `temperature: 0, seed: 42` (its context is the server default) |
| `sampled_seed42` (and `_again`, `_seed7`) | temperature 0.8 with a seed: reproducible sampling? |
| `sampled_no_seed` (and `_again`) | temperature 0.8 without a seed |
| `parallel4_one_by_one` | a server with `OLLAMA_NUM_PARALLEL=4`, one request at a time |
| `parallel4_concurrent` | the same server, four requests at the same moment |

## What you'll see

Recorded on 2026-11-16 on an Apple M4 Pro with 24 GiB of memory, on mains power:
- **Ollama:** 0.40.16, macOS 26.6.2.
- **Models:** `qwen2.5:3b` (digest `357c53fb659c…`, Q4_K_M, trained context 32,768) and `llama3.2:3b` (digest `a80c4f17acd5…`, Q4_K_M, trained context 131,072).
- **Full GPU:** every condition except `cpu_only` had the model 100% on the GPU (`/api/ps`).
- **Two runs:** `determinism.py` was run twice. The two runs produced the same reply, word for word, in every condition except the unseeded ones (which aren't meant to repeat) and one reply in `parallel4_concurrent`.

**What the model files say.** `qwen2.5:3b` carries no parameters of its own (`/api/show` returns an empty `parameters`), so a request without options gets Ollama's defaults. `llama3.2:3b` carries only its three stop tokens. The chat templates are recorded as hashes in `runs/setup.json`: a template is part of what the model reads, and it changes with the model, not with your code.

**The context window you didn't choose.**

| How the model was loaded | Context length allocated (`/api/ps`) |
|---|---|
| native API, no `num_ctx` | 4,096 |
| native API, `num_ctx: 8192` | 8,192 |
| OpenAI-compatible endpoint | 4,096 |

The documentation gives three different defaults on three pages: tiers by GPU memory on the context-length page ("< 24 GiB VRAM: 4k context"), 4,096 in the FAQ, and 2,048 in the Modelfile parameter table. On this machine, 4,096 is what the server allocated. Its log says why: `msg="vram-based default context" total_vram="17.8 GiB" default_num_ctx=4096`. The Mac has 24 GiB of unified memory; Ollama counted 17.8 GiB of it as VRAM, which is the "< 24 GiB" tier. On a machine with more GPU memory, the documented tiers give 32k or 256k: **the same request, unchanged, gets a different context on a different machine.**

A request of 14,835 tokens, the delivery log with the answer in its first line:

| Endpoint | Prompt tokens the model read | Reply |
|---|---|---|
| native, `num_ctx: 16384` (`qwen2.5:3b`) | 14,835 | `Asha` |
| OpenAI-compatible (`qwen2.5:3b`) | 2,050 | `None of the packages (including package 4711) were scanned at any of the hubs` |
| native, `num_ctx: 16384` (`llama3.2:3b`) | 10,960 | `Asha` |
| OpenAI-compatible (`llama3.2:3b`) | 2,050 | `Pune` |

`/v1/chat/completions` returned an ordinary reply with no error. The only sign of the cut is one line in the server log: `level=WARN … msg="truncating input prompt" limit=2050 prompt=14835 keep=4 new=2050`. The model read the end of the log and the question, not the first line, and both models answered wrongly. Ollama's OpenAI compatibility page says "The OpenAI API does not have a way of setting the context size for a model"; its documented workaround is a model created from a Modelfile with `PARAMETER num_ctx`. The server-wide `OLLAMA_CONTEXT_LENGTH` setting changes the default too (context-length page); its effect on this endpoint wasn't measured here. Only `/v1/chat/completions` was tested; the 0.40.16 release notes say a different endpoint, `/v1/responses`, "no longer silently truncates by default".

**Which settings changed the reply.** Replies identical, word for word, to the run they are compared with (8 requests per model):

| Condition | `qwen2.5:3b` | `llama3.2:3b` |
|---|---|---|
| reference procedure again (`cold_again`) | 8/8 | 8/8 |
| model kept loaded (`warm_in_order`) | 8/8 | 8/8 |
| `num_ctx` 4,096 / 16,384 | 8/8, 8/8 | 8/8, 8/8 |
| `num_batch` 64 | 8/8 | 8/8 |
| OpenAI-compatible endpoint (short prompts) | 8/8 | 8/8 |
| `OLLAMA_NUM_PARALLEL=4`, one request at a time | 8/8 | 8/8 |
| same request sent twice in a row (`warm_twice`): first send, second send | 8/8, 7/8 | 7/8, 7/8 |
| **four requests at the same moment** (`parallel4_concurrent`) | **3/8** (both runs) | **5/8**, then **6/8** |
| **CPU only** | **0/8** | **2/8** |
| temperature 0.8, seed 42, run again | 8/8 | 8/8 |
| temperature 0.8, seed 7 instead of 42 | 0/8 | 0/8 |
| temperature 0.8, no seed, run again | 0/8 | 0/8 |

- **The seed works.** At temperature 0.8, the same seed reproduced every reply; a different seed or no seed changed every one, most within the first three words.
- **Context and batch size didn't matter here,** although the server log shows that changing `num_ctx` starts a new runner process, with a different batch size (`-b 512` at 4,096, `-b 1024` at 8,192 for `qwen2.5:3b`).
- **CPU and GPU gave different replies** for 14 of 16 requests. Each was the same in both runs: different, but repeatable.
- **Concurrent requests changed replies, and not repeatably.** With four requests processed together, 3 of 8 (`qwen2.5:3b`) and 5 or 6 of 8 (`llama3.2:3b`) matched the reference, and one `llama3.2:3b` reply differed between the two runs. With the same server handling one request at a time, all 16 matched.
- **The prompt cache changed a few replies.** Run 2 recorded `prompt_eval_cached_count`, the prompt tokens Ollama reused from earlier requests instead of evaluating again. The table takes every temperature-0 reply computed on the GPU one request at a time (`cold_again`, `warm_in_order`, `warm_twice` with its 16 replies, both `num_ctx` conditions, `num_batch_64`, and `parallel4_one_by_one`: 64 replies per model) and groups them by how much of the prompt came from the cache. The cache decides the group sizes: a freshly loaded model has nothing cached, and a request after another one reuses at least the system prompt. Run 1's replies were word for word the same.

  | Prompt tokens reused from the cache (greedy conditions on the GPU, one request at a time) | `qwen2.5:3b` | `llama3.2:3b` |
  |---|---|---|
  | none | 19/19 | 19/19 |
  | the system prompt only | 29/29 | 29/29 |
  | all but the last token (the same request had been sent before) | 15/16 | 14/16 |
  | all | 63/64 | 62/64 |

  Three replies changed when almost the whole prompt came from the cache, the same three in both runs. Each differs from its first word: `llama3.2:3b` started its p1 reply with "Subject: Update on Package 1042 - Delayed Delivery" instead of "Update for Customer Support:".

**Where replies first differ.** A different reply usually starts the same and then takes a different word, and everything after it differs. Under four concurrent requests, `qwen2.5:3b`'s p1 reply began *"Package 1042, which was dispatched from Pune on 14 October at 09:10,"* in both versions, then read *"is expected to arrive in Delhi"* in the reference and *"was scheduled to arrive in Delhi"* under concurrency. The CPU run wrote *"dispatched from Pune at 09:10 on 14 October, was scheduled to arrive"*.

## Limitations

- **One machine, one Ollama version, two small models.** The pattern (which settings change replies) is what to expect elsewhere, not the counts. Other hardware, GPU drivers, or Ollama versions can change which replies differ, and a different machine can change the default context length.
- **Eight requests per condition.** Enough to show that an effect exists, not to estimate its rate.
- **The cause of each change is not traced.** A common explanation is that the same calculation done in a different order (different hardware, batching requests together, evaluating one cached token instead of the whole prompt) rounds differently, and when two candidate words are nearly tied, the rounding decides. That is consistent with what's measured here (replies match for many words and then change), but it wasn't verified inside the runtime.
- **No speed measurements are reported.** These runs shared the machine with other work, and the question here is the reply, not the time.

## Background

The article series' earlier demos already found pieces of this: a fixed seed alone wasn't repeatable in an earlier experiment until the model was unloaded before each run, and an oversized request was silently cut. This demo puts the settings side by side.
