// The two encodings compared: OpenUI Lang (from the library's own serializer) and plain JSON with the same
// component names and props. No model is involved; both are written from the same element tree.
import { jsonToOpenUI } from '@openuidev/lang-core';
import { library } from './library.js';

/** OpenUI Lang, as OpenUI's serializer writes it (positional arguments, one statement). */
export const toOpenUI = (tree) => jsonToOpenUI(tree, library);

/** The same tree as JSON: {"component": name, "props": {...}}, children nested in props.children. */
export function toJsonTree(node) {
  if (Array.isArray(node)) return node.map(toJsonTree);
  if (node && node.type === 'element') {
    const props = Object.fromEntries(Object.entries(node.props).map(([k, v]) => [k, toJsonTree(v)]));
    return { component: node.typeName, props };
  }
  return node;
}

export const toJson = (tree, indent = 0) => JSON.stringify(toJsonTree(tree), null, indent || undefined);

/** Strip parser bookkeeping (statementId, partial, hasDynamicProps) so trees can be compared. */
export function canonical(node) {
  if (Array.isArray(node)) return node.map(canonical);
  if (node && node.type === 'element') {
    return { typeName: node.typeName, props: Object.fromEntries(Object.entries(node.props).map(([k, v]) => [k, canonical(v)])) };
  }
  return node;
}
