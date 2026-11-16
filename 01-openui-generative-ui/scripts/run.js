// Send the twelve requests to local models under four conditions, and save every reply with its checks and timings.
//
//   node scripts/run.js --models qwen2.5:3b,llama3.2:3b --out runs/run1
//   node scripts/run.js --models qwen2.5:3b --only r01,r07 --out runs/trial   (a subset of requests)
//
// Conditions, in this order for each model (the model is unloaded first, then requests run in a fixed order):
//   openui           OpenUI's generated system prompt, free text, parsed by OpenUI's parser
//   openui-examples  the same prompt with two examples (data/examples.js) through the `examples` option
//   openui-repair    as openui, then up to 2 repairs: the parser's problems are sent back to the model
//   json             the same components as JSON, constrained by a JSON schema through Ollama's `format`
import { mkdirSync, writeFileSync } from 'node:fs';
import { EXAMPLES } from '../data/examples.js';
import { REQUESTS } from '../data/requests.js';
import { jsonProblems, missingValues, newParser, newStreamingParser, openuiProblems } from '../src/check.js';
import { JSON_FORMAT, JSON_SYSTEM_PROMPT } from '../src/json-mode.js';
import { library } from '../src/library.js';
import { chat, OPTIONS, unload } from '../src/ollama.js';

const arg = (name, fallback) => (process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback);
const MODELS = arg('--models', 'qwen2.5:3b,llama3.2:3b').split(',');
const OUT = arg('--out', 'runs/latest');
const CONDITIONS = arg('--conditions', 'openui,openui-examples,openui-repair,json').split(',');
const MAX_REPAIRS = 2;
const ONLY = arg('--only', '');
const ITEMS = ONLY ? REQUESTS.filter((r) => ONLY.split(',').some((id) => r.id.startsWith(id))) : REQUESTS;

for (const example of EXAMPLES) {
  const problems = openuiProblems(newParser().parse(example));
  if (problems.length) throw new Error(`An example does not parse cleanly: ${problems.join('; ')}`);
}
const PROMPTS = { openui: library.prompt(), 'openui-examples': library.prompt({ examples: EXAMPLES }) };
PROMPTS['openui-repair'] = PROMPTS.openui;

/** First moment the streamed reply renders at least one complete component (OpenUI's streaming parser). */
function streamWatcher() {
  const parser = newStreamingParser();
  let firstComponentMs = null;
  return {
    onText(text, ms) {
      if (firstComponentMs !== null) return;
      const children = parser.set(text).root?.props?.children;
      if (Array.isArray(children) && children.some((c) => c && c.type === 'element' && !c.partial)) firstComponentMs = ms;
    },
    get firstComponentMs() {
      return firstComponentMs;
    },
  };
}

async function runOpenUI(model, condition, item) {
  const messages = [
    { role: 'system', content: PROMPTS[condition] },
    { role: 'user', content: item.request },
  ];
  const attempts = [];
  for (let attempt = 0; attempt <= (condition === 'openui-repair' ? MAX_REPAIRS : 0); attempt++) {
    const watcher = streamWatcher();
    const reply = await chat(model, messages, { onText: watcher.onText });
    const result = newParser().parse(reply.text);
    const problems = openuiProblems(result);
    attempts.push({ ...reply, firstComponentMs: watcher.firstComponentMs, problems, missing: missingValues(result.root, item.values) });
    if (!problems.length) break;
    messages.push({ role: 'assistant', content: reply.text });
    messages.push({
      role: 'user',
      content: `The openui-lang parser reported these problems with your reply:\n${problems.map((p) => `- ${p}`).join('\n')}\nReply again with the complete, corrected openui-lang program only.`,
    });
  }
  return attempts;
}

async function runJson(model, item) {
  const reply = await chat(
    model,
    [
      { role: 'system', content: JSON_SYSTEM_PROMPT },
      { role: 'user', content: item.request },
    ],
    { format: JSON_FORMAT },
  );
  const { problems, tree } = jsonProblems(reply.text);
  return [{ ...reply, firstComponentMs: null, problems, missing: tree ? missingValues(tree, item.values) : item.values }];
}

mkdirSync(OUT, { recursive: true });
const ollamaVersion = await fetch(`${process.env.OLLAMA_URL ?? 'http://localhost:11434'}/api/version`).then((r) => r.json());
const tags = await fetch(`${process.env.OLLAMA_URL ?? 'http://localhost:11434'}/api/tags`).then((r) => r.json());
writeFileSync(
  `${OUT}/setup.json`,
  JSON.stringify(
    {
      startedAt: new Date().toISOString(),
      node: process.version,
      ollama: ollamaVersion.version,
      models: MODELS.map((m) => ({ model: m, digest: tags.models.find((t) => t.name === m)?.digest })),
      options: OPTIONS,
      conditions: CONDITIONS,
      requests: ITEMS.map((r) => r.id),
      prompts: { openui: PROMPTS.openui, 'openui-examples': PROMPTS['openui-examples'], json: JSON_SYSTEM_PROMPT },
      jsonFormat: JSON_FORMAT,
    },
    null,
    2,
  ),
);

for (const model of MODELS) {
  await unload(model);
  const lines = [];
  for (const condition of CONDITIONS) {
    for (const item of ITEMS) {
      const attempts = condition === 'json' ? await runJson(model, item) : await runOpenUI(model, condition, item);
      const last = attempts.at(-1);
      lines.push(JSON.stringify({ model, condition, id: item.id, attempts }));
      console.log(
        `${model.padEnd(12)} ${condition.padEnd(16)} ${item.id.padEnd(20)} ` +
          `${last.problems.length ? 'PROBLEMS' : 'clean   '} missing ${String(last.missing.length).padStart(2)}  ` +
          `attempts ${attempts.length}  ${last.outputTokens} tokens  ${Math.round(last.totalMs)} ms` +
          (last.problems.length ? `  ${last.problems[0].slice(0, 90)}` : ''),
      );
    }
  }
  writeFileSync(`${OUT}/${model.replace(/[:/]/g, '_')}.jsonl`, lines.join('\n') + '\n');
  await unload(model);
}
console.log(`\nSaved to ${OUT}/`);
