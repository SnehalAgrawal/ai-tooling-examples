// Summarise saved runs: per model and condition, how many replies parse cleanly, contain every value, or both;
// what the repair loop changed; output tokens; and, for OpenUI, time to the first complete component.
//
//   node scripts/summarise.js runs/run1 [runs/run2]     (with two runs, also compares them reply by reply)
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { LABELS } from '../data/labels.js';

const load = (dir) =>
  readdirSync(dir)
    .filter((f) => f.endsWith('.jsonl'))
    .flatMap((f) => readFileSync(`${dir}/${f}`, 'utf8').trim().split('\n').map((l) => JSON.parse(l)));
const median = (xs) => {
  const s = xs.filter((x) => x != null).sort((a, b) => a - b);
  return s.length ? s[Math.floor((s.length - 1) / 2)] : null;
};
const [dir, compareDir] = process.argv.slice(2);
const records = load(dir);
const models = [...new Set(records.map((r) => r.model))];
const conditions = [...new Set(records.map((r) => r.condition))];
const ok = (a) => a.problems.length === 0;
const full = (a) => a.missing.length === 0;
const sha = (text) => createHash('sha256').update(text).digest('hex').slice(0, 12);

// Hand labels apply only to the exact reply that was read; a clean reply without a matching label stops the summary
function asked(r) {
  const a = r.attempts.at(-1);
  if (!ok(a)) return false;
  const label = LABELS[`${r.model}|${r.condition}|${r.id}`];
  if (!label) throw new Error(`no hand label for clean reply ${r.model}|${r.condition}|${r.id}`);
  if (label.sha !== sha(a.text)) throw new Error(`hand label for ${r.model}|${r.condition}|${r.id} is for a different reply`);
  return label.asked;
}

for (const model of models) {
  console.log(`\n${model} (${records.filter((r) => r.model === model && r.condition === conditions[0]).length} requests per condition)`);
  console.log(`  ${'condition'.padEnd(16)} ${'clean'.padStart(6)} ${'values'.padStart(7)} ${'both'.padStart(5)} ${'as asked'.padStart(9)}  ${'tokens (median)'.padStart(15)}  first component / whole reply (median ms)`);
  for (const condition of conditions) {
    const rs = records.filter((r) => r.model === model && r.condition === condition);
    const last = rs.map((r) => r.attempts.at(-1));
    const n = rs.length;
    const stream = condition === 'json' ? '' : `${median(last.map((a) => a.firstComponentMs && Math.round(a.firstComponentMs)))} / ${median(last.map((a) => Math.round(a.totalMs)))}`;
    console.log(
      `  ${condition.padEnd(16)} ${`${last.filter(ok).length}/${n}`.padStart(6)} ${`${last.filter(full).length}/${n}`.padStart(7)} ${`${last.filter((a) => ok(a) && full(a)).length}/${n}`.padStart(5)} ${`${rs.filter(asked).length}/${n}`.padStart(9)}  ${String(median(last.map((a) => a.outputTokens))).padStart(15)}  ${stream}`,
    );
    if (condition === 'openui-repair') {
      const repaired = rs.filter((r) => r.attempts.length > 1);
      const changed = repaired.filter((r) => r.attempts.slice(1).some((a) => a.text !== r.attempts[0].text));
      const fixed = repaired.filter((r) => ok(r.attempts.at(-1)));
      console.log(`    repair: ran on ${repaired.length}; reply changed on ${changed.length}; ended clean on ${fixed.length} (${fixed.map((r) => r.id.slice(0, 3)).join(' ') || '-'})`);
      // Same prompt and seed as the plain condition, so the first attempts should match it exactly
      const plain = records.filter((r) => r.model === model && r.condition === 'openui');
      const sameFirst = rs.filter((r) => plain.find((p) => p.id === r.id)?.attempts[0].text === r.attempts[0].text);
      console.log(`    first attempts identical to the openui condition's replies: ${sameFirst.length}/${n}`);
    }
  }
  // Output tokens for the same request, where both the OpenUI-with-examples and the JSON reply show what was asked
  const pairs = records
    .filter((r) => r.model === model && r.condition === 'openui-examples')
    .map((r) => [r, records.find((j) => j.model === model && j.condition === 'json' && j.id === r.id)])
    .filter(([o, j]) => j && asked(o) && asked(j));
  if (pairs.length) {
    const o = pairs.reduce((s, [r]) => s + r.attempts.at(-1).outputTokens, 0);
    const j = pairs.reduce((s, [, r]) => s + r.attempts.at(-1).outputTokens, 0);
    console.log(`  output tokens where openui-examples and json both show what was asked (${pairs.length} requests): openui ${o}, json ${j} (openui ${Math.round((100 * (j - o)) / j)}% fewer)`);
  }
  const problems = records
    .filter((r) => r.model === model && r.condition !== 'json')
    .flatMap((r) => r.attempts.at(-1).problems.map((p) => p.split(':')[0]));
  const counts = Object.entries(problems.reduce((c, p) => ({ ...c, [p]: (c[p] ?? 0) + 1 }), {})).sort((a, b) => b[1] - a[1]);
  console.log(`  OpenUI problem kinds (final replies, all OpenUI conditions): ${counts.map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}`);
}

if (compareDir) {
  const other = load(compareDir);
  const key = (r) => `${r.model}|${r.condition}|${r.id}`;
  const byKey = new Map(other.map((r) => [key(r), r]));
  const same = records.filter((r) => {
    const o = byKey.get(key(r));
    return o && o.attempts.length === r.attempts.length && o.attempts.every((a, i) => a.text === r.attempts[i].text);
  });
  console.log(`\nRepeatability: ${same.length} of ${records.length} requests got identical replies (every attempt) in ${dir} and ${compareDir}`);
  for (const r of records.filter((x) => !same.includes(x))) console.log(`  differs: ${key(r)}`);
}
