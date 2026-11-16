// Tokens for the same eight interfaces in OpenUI Lang and in JSON, with two OpenAI tokenizers.
// Each OpenUI Lang string is parsed back and must equal its source tree, so both encodings carry the same interface.
//
//   npm run tokens
import { isDeepStrictEqual } from 'node:util';
import { createParser } from '@openuidev/lang-core';
import { Tiktoken } from 'js-tiktoken/lite';
import o200k from 'js-tiktoken/ranks/o200k_base';
import cl100k from 'js-tiktoken/ranks/cl100k_base';
import { INTERFACES } from '../data/interfaces.js';
import { canonical, toJson, toOpenUI } from '../src/encode.js';
import { library, ROOT } from '../src/library.js';

const TOKENIZERS = { o200k_base: new Tiktoken(o200k), cl100k_base: new Tiktoken(cl100k) };
const parser = createParser(library.toJSONSchema(), ROOT);
const count = (enc, text) => enc.encode(text).length;
const pct = (a, b) => `${Math.round((100 * (b - a)) / b)}%`;

for (const { id, tree } of INTERFACES) {
  const result = parser.parse(toOpenUI(tree));
  const ok = result.meta.errors.length === 0 && !result.meta.incomplete && isDeepStrictEqual(canonical(result.root), canonical(tree));
  if (!ok) throw new Error(`${id}: OpenUI Lang did not parse back to the same tree`);
}
console.log(`All ${INTERFACES.length} OpenUI Lang strings parse back to their source trees.\n`);

for (const [name, enc] of Object.entries(TOKENIZERS)) {
  console.log(`Tokenizer ${name}`);
  console.log(`  ${'interface'.padEnd(16)} ${'OpenUI'.padStart(7)} ${'JSON'.padStart(6)} ${'JSON, indented'.padStart(15)}   OpenUI saves vs JSON / indented`);
  const totals = [0, 0, 0];
  for (const { id, tree } of INTERFACES) {
    const n = [count(enc, toOpenUI(tree)), count(enc, toJson(tree)), count(enc, toJson(tree, 2))];
    n.forEach((v, i) => (totals[i] += v));
    console.log(`  ${id.padEnd(16)} ${String(n[0]).padStart(7)} ${String(n[1]).padStart(6)} ${String(n[2]).padStart(15)}   ${pct(n[0], n[1]).padStart(4)} / ${pct(n[0], n[2])}`);
  }
  console.log(`  ${'all'.padEnd(16)} ${String(totals[0]).padStart(7)} ${String(totals[1]).padStart(6)} ${String(totals[2]).padStart(15)}   ${pct(totals[0], totals[1]).padStart(4)} / ${pct(totals[0], totals[2])}\n`);
}

const prompt = library.prompt(); // the system prompt OpenUI generates for this library, no examples
console.log(`System prompt for this six-component library: ${prompt.length} characters, ` +
  Object.entries(TOKENIZERS).map(([name, enc]) => `${count(enc, prompt)} tokens (${name})`).join(', '));

if (process.argv.includes('--show')) {
  const { tree } = INTERFACES.find((i) => i.id === (process.argv[process.argv.indexOf('--show') + 1] ?? 'bar-chart'));
  console.log(`\n${toOpenUI(tree)}\n\n${toJson(tree)}`);
}
