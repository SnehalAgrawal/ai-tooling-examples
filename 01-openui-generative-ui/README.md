# OpenUI, measured

[OpenUI](https://www.openui.com) lets a model answer with an interface instead of text: you define the components it may use, OpenUI turns them into a system prompt, the model writes *OpenUI Lang*, and a parser turns that into a component tree you render. This demo measures three things about it, with no API keys and no hosted service:

1. **Tokens.** The same eight interfaces in OpenUI Lang and as JSON, counted with two OpenAI tokenizers, plus Thesys's own benchmark files counted again.
2. **Whether small local models write valid OpenUI Lang.** Twelve requests to `llama3.2:3b` and `qwen2.5:3b`: with OpenUI's generated prompt, with two examples added, with a repair loop, and, for comparison, as schema-constrained JSON. Every reply that passed the automatic checks was also read and labelled by hand.
3. **Streaming.** How far into a reply the first complete component appears.

It accompanies an article in the AI Tooling track on [snehalagrawal.com](https://snehalagrawal.com/articles/) (in progress).

## Run it

Requirements: Node.js 20 or newer, and [Ollama](https://ollama.com) for parts 2 and 3.

```bash
OPENUI_TELEMETRY_DISABLED=1 npm ci        # see "Install telemetry" below
npm run tokens                            # part 1: no model needed, under a second

ollama pull llama3.2:3b && ollama pull qwen2.5:3b
node scripts/run.js --out runs/run1       # parts 2 and 3: 96 requests, under 3 minutes on an M4 Pro
node scripts/run.js --out runs/run2       # again, to compare
node scripts/summarise.js runs/run1 runs/run2
```

`run.js` unloads each model before its requests and again afterwards, sends every request with temperature 0, seed 42, and a context of 8,192 tokens, and saves every reply with its checks and timings to `runs/` (git-ignored). `summarise.js` refuses to report a reply the automatic checks accepted unless it has a hand label for exactly that reply ([`data/labels.js`](data/labels.js), matched by a hash of the reply text), so changing the code or the models means reading the new replies.

### Install telemetry

`@openuidev/lang-core` runs a `postinstall` script that sends install telemetry to PostHog by default: a random installation ID, a salted project hash, and version, OS, and CI details (its own disclosure says no code, prompts, or personal data). `OPENUI_TELEMETRY_DISABLED=1` or `DO_NOT_TRACK=1` turns it off. Runtime telemetry is off unless you set `OPENUI_RUNTIME_TELEMETRY_ENABLED`.

## How it works

- **The components** ([`src/library.js`](src/library.js)): a `Stack` holding five kinds of component (`Heading`, `Text`, `Metric`, `Table`, `BarChart`), defined with Zod. `library.prompt()` generates the system prompt from them (2,864 characters, 627 tokens with `o200k_base`), and OpenUI's parser checks replies against the same definitions.
- **The JSON baseline** ([`src/json-mode.js`](src/json-mode.js)): the same components, with a prompt that lists them in the same notation, and a JSON schema passed to Ollama's `format`, which constrains decoding to the schema.
- **The checks** ([`src/check.js`](src/check.js)), kept separate on purpose:
  - **Clean:** OpenUI's parser reports no errors, there is a `root`, every reference is defined, nothing is cut off, and nothing defined is left unshown. For JSON: it parses, and every component matches its schema strictly.
  - **Values:** every value the request contained (numbers, names, labels) appears somewhere in the interface. This is "all values present", not correctness.
  - **As asked** (by hand): the interface shows everything the request asked for, each requested element, sentence, and value, and nothing wrong. Extra components are allowed.
- **Streaming:** OpenUI's streaming parser re-parses the reply after every chunk; the time to the first complete component is recorded within each request.

## What you'll see

Recorded on 2026-11-16 (UTC) on an Apple M4 Pro, on battery: Node.js 24.16.0, Ollama 0.40.16, `llama3.2:3b` (digest `a80c4f17acd5…`) and `qwen2.5:3b` (digest `357c53fb659c…`), `@openuidev/lang-core` 1.1.1. No other model workload ran during the runs (checked every 5 seconds). **The two runs produced identical replies for all 96 requests, every attempt included**; timings differed slightly, as timings do.

### 1. Tokens: the saving is in the structure, and JSON's whitespace

The same eight interfaces ([`data/interfaces.js`](data/interfaces.js)), written once as an element tree, then as OpenUI Lang by OpenUI's own serializer (`jsonToOpenUI`; each result is parsed back and must equal the tree) and as JSON with the same component names and props:

| Interface | OpenUI Lang | JSON, compact | JSON, indented | OpenUI saves vs compact / indented |
|---|---:|---:|---:|---|
| a single metric | 15 | 29 | 58 | 48% / 74% |
| three metrics | 39 | 76 | 153 | 49% / 75% |
| a bar chart of five values | 52 | 68 | 136 | 24% / 62% |
| a sentence and a three-row table | 63 | 78 | 155 | 19% / 59% |
| the same eight values as a chart and a table | 140 | 150 | 310 | 7% / 55% |
| heading, three metrics, a chart, and a table | 170 | 201 | 403 | 15% / 58% |
| a 12-row, 4-column table | 195 | 179 | 364 | **−9%** / 46% |
| two bar charts | 118 | 125 | 230 | 6% / 49% |
| **all eight** | **792** | **906** | **1,809** | **13% / 56%** |

Tokens with `o200k_base` (the GPT-4o family's tokenizer, through `js-tiktoken` 1.0.21). With `cl100k_base`: 8% / 56% overall.

OpenUI Lang passes arguments by position, so it saves the key names JSON repeats; data costs about the same in both. On the data-heavy table it is *larger* than compact JSON, partly because the serializer writes `", "` between values where compact JSON writes `","`.

**Thesys's benchmark, recounted.** OpenUI's README reports 4,800 tokens for OpenUI Lang against 9,948 for "Thesys C1 JSON" (−51.7%) on seven scenarios. Counting their committed sample files (`benchmarks/samples` at commit `f21bcb5`) with `o200k_base`, the tokenizer `tiktoken` uses for `gpt-5`:

| Format | Tokens | OpenUI Lang compared |
|---|---:|---|
| OpenUI Lang | 4,800 (the same as their README) | |
| C1 JSON, as committed (indented) | 9,650 (their README: 9,948) | −50.3% |
| C1 JSON, whitespace removed | 5,461 | **−12.1%** |

The C1 count differs from their README by 3%; the cause was not found (their script was not run here). Either way, most of the published saving is JSON indentation. Against compact JSON, OpenUI Lang saved 12% on their samples and 13% on this demo's.

**In real replies.** On the requests where both the OpenUI reply (with examples) and the JSON reply showed what was asked, the OpenUI replies used fewer output tokens, counted by Ollama with each model's own tokenizer: 444 against 638 for `llama3.2:3b` (7 requests, 30% fewer) and 256 against 431 for `qwen2.5:3b` (5 requests, 41% fewer). The models wrote JSON with spaces after colons and commas, between compact and indented.

### 2. Validity: small models need examples, and the repair loop barely helps

Twelve requests ([`data/requests.js`](data/requests.js)), each carrying its data. Out of 12:

| `llama3.2:3b` | Clean | All values present | Clean and all values | **As asked (by hand)** |
|---|---:|---:|---:|---:|
| OpenUI, generated prompt | 0 | 0 | 0 | **0** |
| OpenUI, plus two examples | 9 | 10 | 9 | **8** |
| OpenUI, plus up to 2 repairs | 1 | 0 | 0 | **0** |
| JSON, schema-constrained | 12 | 11 | 11 | **9** |

| `qwen2.5:3b` | Clean | All values present | Clean and all values | **As asked (by hand)** |
|---|---:|---:|---:|---:|
| OpenUI, generated prompt | 2 | 3 | 2 | **2** |
| OpenUI, plus two examples | 6 | 6 | 5 | **5** |
| OpenUI, plus up to 2 repairs | 2 | 4 | 2 | **2** |
| JSON, schema-constrained | 12 | 11 | 11 | **10** |

**What went wrong with OpenUI's generated prompt alone.**
- **`llama3.2:3b` wrote named arguments** in 11 of 12 replies, `Stack(children: [Metric(label: "…", value: "…")])`, although the prompt says, in capitals, that arguments are positional and colon syntax is not supported. The components and values inside were mostly right.
- **`qwen2.5:3b` failed in more ways:** no `root` statement (r01); named arguments with `=` or `:` (r03, r10); a table written as one array with the header as the first row (r04, r06, r09); numbers in table cells, which this library types as strings (r08); an invented argument (r12); metrics nested inside a table (r05).
- **The parser reports these precisely**, for example `excess-args: BarChart takes 3 arg(s), got 17` or `type-mismatch: field "/values/0" expects number but got array`.

**Two examples changed the most** (from 0 to 8 for `llama3.2:3b`), at the cost of a longer prompt. The examples are about unrelated data ([`data/examples.js`](data/examples.js)), so no value could be copied from them.

**The repair loop** sent the parser's problems back to the model, up to twice. It ran on 12 and 10 requests; the reply changed on 8 and 7; one ended clean, and that one (`llama3.2:3b`, r05) had lost the company names. On 4 requests (`llama3.2:3b`) and 3 (`qwen2.5:3b`) the model sent the identical reply all three times. As in the [LangGraph Text-to-SQL example](https://github.com/SnehalAgrawal/data-engineering-patterns/tree/main/langgraph-text-to-sql), a small model shown its own errors mostly repeated them.

**JSON was always valid, and still not always right.** Constrained decoding guarantees the structure, so all 24 replies passed the schema. Reading them found what a schema cannot catch:
- `qwen2.5:3b`, r08: the last table cell holds `199]]}}]}}user\nCould you also provide a bar chart…`. The model ran on inside a string into an invented next turn; as a string, it matched the schema, and the values check found "199".
- `llama3.2:3b`, r07: an added sentence, "Total packages: 3000 + 2543 + 457 = 5000".
- `llama3.2:3b`, r09: a metric "Average Delivery Time" showing 39.7, which is one company's value.
- A requested sentence missing (`llama3.2:3b`, r12) or turned into a question that does not answer it (`qwen2.5:3b`, r05).

**What OpenUI's parser accepts without an error** (lang-core 1.1.1; each checked directly):
- **Prose before the code is dropped.** `llama3.2:3b` (with examples, r05) put the requested sentence before the program; it parsed cleanly and the sentence was never shown.
- **A second `root =` replaces the first.** `llama3.2:3b` wrote two complete programs twice; the second was rendered.
- **A `Stack` inside a `Stack`** is accepted, although this library declares a Stack's children as the five other components.
- **A component with a type error stays in the tree;** the error is listed, and what to render is up to you.

### 3. Streaming: it pays only in the multi-statement style

OpenUI's prompt asks for `root = Stack([a, b, …])` first, then one statement per component, so the layout arrives first and components fill in. Measured within each clean reply (with examples, both models, both runs):

- **Several statements:** the first complete component appeared 24% to 59% of the way through the reply.
- **One component, or everything inline in one statement:** 98% to 99%. Written inline, `root = Stack([Heading(…), Metric(…)])` produces nothing complete until the statement ends; a direct test confirmed this character by character.
- **In time:** these replies are short (19 to 111 tokens, about 1 second), so the first component came 0.2 to 1.9 seconds after the first token. The benefit grows with longer replies; that was not measured here.

While a later statement is still streaming, the parser marks the whole tree as partial, including components that are already complete (their values stay in place). A renderer that hides partial components would hide finished ones for a moment; how OpenUI's own renderers handle it was not tested.

## Limitations

- **Two 3-billion-parameter models, twelve requests, one library.** These numbers show how small local models handle a new UI language and where the checks are blind. They don't tell us how larger or hosted models would perform: none was tested.
- **The prompt is OpenUI's.** It was used as generated, plus examples in one condition. A different prompt could change the results, in either direction.
- **Table cells are typed as strings** in this library, a choice a model can trip over; constrained JSON cannot. Accepting numbers as well would likely remove several failures. Not measured.
- **No renderer.** The demo parses and checks interfaces; it does not draw them. "As asked" was judged from the component tree.
- **Token counts depend on the tokenizer and the formatting.** Two OpenAI tokenizers for part 1; each model's own tokenizer for real replies.
