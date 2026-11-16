// Checks on a reply. "Parses cleanly" and "all values present" are separate on purpose: an interface can parse
// with no errors and still drop or change a number, and a broken one can still contain every value.
import { createParser, createStreamingParser } from '@openuidev/lang-core';
import { COMPONENTS, library, ROOT } from './library.js';

const schema = library.toJSONSchema();
export const newParser = () => createParser(schema, ROOT);
export const newStreamingParser = () => createStreamingParser(schema, ROOT);

/** Problems that stop an OpenUI Lang reply from rendering as written; an empty list means it parses cleanly. */
export function openuiProblems(result) {
  const problems = result.meta.errors.map((e) => `${e.code ?? 'error'}: ${e.message}`);
  if (!result.root) problems.push('no root: nothing to render');
  else if (result.root.typeName !== ROOT) problems.push(`root is ${result.root.typeName}, expected ${ROOT}`);
  if (result.meta.unresolved.length) problems.push(`unresolved references: ${result.meta.unresolved.join(', ')}`);
  if (result.meta.incomplete) problems.push('incomplete: the reply ends mid-statement');
  if (result.meta.orphaned.length) problems.push(`defined but not shown: ${result.meta.orphaned.join(', ')}`);
  return problems;
}

const PROPS = Object.fromEntries(COMPONENTS.map((c) => [c.name, c.props]));

/** The same rules for a JSON reply ({"component", "props"}), using the library's own prop schemas, strictly. */
export function jsonProblems(text) {
  let tree;
  try {
    tree = JSON.parse(text);
  } catch (e) {
    return { problems: [`not JSON: ${e.message}`], tree: null };
  }
  const problems = [];
  const visit = (node, path) => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return problems.push(`${path}: not a component object`);
    const props = PROPS[node.component];
    if (!props) return problems.push(`${path}: unknown component ${JSON.stringify(node.component)}`);
    if (node.component === ROOT) {
      // A Stack's children are checked one by one below; here only its own shape
      const extra = Object.keys(node.props ?? {}).filter((k) => k !== 'children');
      if (!Array.isArray(node.props?.children)) problems.push(`${path} ${ROOT}: children must be an array`);
      if (extra.length) problems.push(`${path} ${ROOT}: unknown props ${extra.join(', ')}`);
      if (path !== 'root') problems.push(`${path}: ${ROOT} is only allowed as the root`);
      (Array.isArray(node.props?.children) ? node.props.children : []).forEach((child, i) => visit(child, `${path}.children[${i}]`));
      return;
    }
    const parsed = props.strict().safeParse(node.props ?? {});
    if (!parsed.success) problems.push(`${path} ${node.component}: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`);
  };
  visit(tree, 'root');
  if (tree?.component !== ROOT) problems.push(`root is ${JSON.stringify(tree?.component)}, expected ${ROOT}`);
  return { problems, tree };
}

/** Every string and number in a tree (OpenUI element nodes or JSON components), for the values check. */
export function leafValues(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => leafValues(n, out));
  else if (node && typeof node === 'object') Object.values(node.props ?? node).forEach((v) => leafValues(v, out));
  else if (typeof node === 'string' || typeof node === 'number') out.push(node);
  return out;
}

// A whole value that is a number ("2,543", "2543", 2543, "84.8%") compares by value, keeping the percent sign
const asNumber = (v) =>
  typeof v === 'number' ? v : /^-?[\d,]+(\.\d+)?%?$/.test(String(v).trim()) ? Number(String(v).replace(/[,%]/g, '')) : NaN;
const isPercent = (v) => String(v).trim().endsWith('%');
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Expected values that appear nowhere in the interface. Measures "all values present", not correctness. */
export function missingValues(tree, expected) {
  const leaves = leafValues(tree);
  return expected.filter((want) => {
    const n = asNumber(want);
    const text = String(want);
    // Inside longer text, match whole words; short values such as "IN" are case-sensitive ("in transit" is not India)
    const word = new RegExp(`(^|[^\\w])${escape(text)}([^\\w]|$)`, text.length <= 3 ? '' : 'i');
    return !leaves.some((have) => {
      if (!Number.isNaN(n) && asNumber(have) === n && isPercent(have) === isPercent(want)) return true;
      return word.test(String(have));
    });
  });
}
