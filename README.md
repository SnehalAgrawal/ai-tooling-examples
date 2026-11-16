# ai-tooling-examples

Runnable demos for the AI Tooling articles on [snehalagrawal.com](https://snehalagrawal.com/articles/): hands-on, measured looks at specific AI tools and frameworks. What each tool is for, what it does under the hood, and where it breaks, with numbers from recorded runs. Each demo is self-contained and runs locally, with [Ollama](https://ollama.com) where a model is needed (and Docker for the self-hosted Langfuse): no API keys or paid services.

| Demo | Tool | What it shows |
|---|---|---|
| [01-openui-generative-ui](01-openui-generative-ui/) | [OpenUI](https://www.openui.com) | Generative UI measured: tokens against JSON (including Thesys's own benchmark files, recounted), how often small local models write valid OpenUI Lang with and without examples or a repair loop, schema-constrained JSON for comparison, and what streaming buys |
| [02-langfuse-tracing](02-langfuse-tracing/) | [Langfuse](https://langfuse.com) | Tracing and evaluation with a self-hosted Langfuse, every claim read back through the API: what a trace contains, overhead per request, what happens when Langfuse is down or slow, short-lived scripts, scores on dataset runs, and which masking hook masks |
| [03-mcp-tools](03-mcp-tools/) | [Model Context Protocol](https://modelcontextprotocol.io) | An MCP server and host for a delivery fleet, every JSON-RPC message logged: tool use by three small models, tool descriptions, an injected instruction and a poisoned tool description, and stdio against Streamable HTTP |
| [04-ollama-reproducibility](04-ollama-reproducibility/) | [Ollama](https://ollama.com) | Which settings change a local model's reply: reloads, the prompt cache, context and batch size, CPU against GPU, concurrent requests, seeds, and the OpenAI-compatible endpoint; plus the context length the server actually allocates |
| [05-langchain-what-it-hides](05-langchain-what-it-hides/) | [LangChain](https://docs.langchain.com/oss/python/langchain/overview) | A logging proxy between LangChain and Ollama: what `ChatOllama` sends, drops, and ignores, structured output measured on three models, errors and retries, and the cost of the abstraction |

## Linting

```bash
npm install          # ESLint (repository root)
npm run lint         # ESLint for the JavaScript demos, then Ruff for the Python ones (needs `ruff` on PATH)
```

## Conventions

- Every demo has a README with the exact setup (versions, model digests, settings, date) that produced its numbers.
- Versions are pinned, and each demo commits its lockfile: these tools change fast.
- Generated files (`runs/`) and `.env` files are not committed.
