// The baseline: the same six components as JSON, with Ollama's schema-constrained output (`format`).
// Prompt and schema are generated from the same definitions as OpenUI's, so both modes offer the same choices.
import { z } from 'zod';
import { COMPONENTS, ROOT } from './library.js';

const leaves = COMPONENTS.filter((c) => c.name !== ROOT);
const propsSchema = (c) => {
  const { $schema: _drop, ...schema } = z.toJSONSchema(c.props.strict());
  return schema;
};

/** JSON schema for a Stack whose children are any of the other five components. */
export const JSON_FORMAT = {
  type: 'object',
  properties: {
    component: { const: ROOT },
    props: {
      type: 'object',
      properties: {
        children: {
          type: 'array',
          items: {
            anyOf: leaves.map((c) => ({
              type: 'object',
              properties: { component: { const: c.name }, props: propsSchema(c) },
              required: ['component', 'props'],
              additionalProperties: false,
            })),
          },
        },
      },
      required: ['children'],
      additionalProperties: false,
    },
  },
  required: ['component', 'props'],
  additionalProperties: false,
};

// Types written like OpenUI's prompt signatures (string, number[], string[][]), so neither prompt is easier to read
const typeName = (schema) => (schema.type === 'array' ? `${typeName(schema.items)}[]` : schema.type);
const describe = (c) => {
  const props = Object.entries(c.props.shape).map(([name, schema]) => `${name}: ${typeName(z.toJSONSchema(schema))}`);
  return `- ${c.name} {${props.join(', ')}}: ${c.description}`;
};

export const JSON_SYSTEM_PROMPT = `You answer with a user interface described as JSON. Your entire response must be one JSON object, with no other text.

The root is {"component": "Stack", "props": {"children": [...]}}. Each child is {"component": "<name>", "props": {...}} using one of these components:
${leaves.map(describe).join('\n')}

Choose the components that best represent the content (tables for comparisons, charts for numbers to compare, metrics for headline numbers).`;
