// The component library the model may use: a Stack holding five kinds of component, for answering questions
// about data. OpenUI turns these definitions into the system prompt, and its parser checks replies against them.
import { createLibrary, defineComponent } from '@openuidev/lang-core';
import { z } from 'zod';

const component = (name, description, props) =>
  defineComponent({ name, description, props: z.object(props), component: null }); // no renderer needed to parse

const LEAVES = [
  component('Heading', 'A title line', { text: z.string() }),
  component('Text', 'A short paragraph of plain text', { text: z.string() }),
  component('Metric', 'One headline number with a label', { label: z.string(), value: z.string() }),
  component('Table', 'A table: column names, then rows of cell values as strings', {
    columns: z.array(z.string()),
    rows: z.array(z.array(z.string())),
  }),
  component('BarChart', 'A bar chart of labelled numeric values', {
    title: z.string(),
    labels: z.array(z.string()),
    values: z.array(z.number()),
  }),
];

// Children must be components (`.ref` is OpenUI's way to say so), not nested arrays or loose values
const STACK = component('Stack', 'Vertical layout that holds other components, top to bottom', {
  children: z.array(z.union(LEAVES.map((c) => c.ref))),
});

export const ROOT = 'Stack';
export const COMPONENTS = [STACK, ...LEAVES];
export const library = createLibrary({ root: ROOT, components: COMPONENTS });
