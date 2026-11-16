# ai-tooling-examples

Runnable demos for the AI Tooling articles on [snehalagrawal.com](https://snehalagrawal.com/articles/): hands-on, measured looks at specific AI tools and frameworks. What each tool is for, what it does under the hood, and where it breaks, with numbers from recorded runs. Each demo is self-contained and runs locally, with [Ollama](https://ollama.com) where a model is needed: no API keys or paid services.

| Demo | Tool | What it shows |
|---|---|---|
| [01-openui-generative-ui](01-openui-generative-ui/) | [OpenUI](https://www.openui.com) | Generative UI measured: tokens against JSON (including Thesys's own benchmark files, recounted), how often small local models write valid OpenUI Lang with and without examples or a repair loop, schema-constrained JSON for comparison, and what streaming buys |

## Linting

```bash
npm install          # ESLint (repository root)
npm run lint
```

## Conventions

- Every demo has a README with the exact setup (versions, model digests, settings, date) that produced its numbers.
- Versions are pinned, and each demo commits its lockfile: these tools change fast.
- Generated files (`runs/`) and `.env` files are not committed.
