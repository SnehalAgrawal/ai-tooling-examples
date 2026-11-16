// Hand labels for every reply the automatic checks could not judge: each reply that parsed cleanly (OpenUI) or
// passed the schema (JSON). Read on 2026-11-16 from runs/run1 (runs/run2 was identical, reply by reply).
// "asked" means the interface shows everything the request asked for (each requested element, sentence, and
// value) and nothing wrong; extra components are allowed. `sha` is the first 12 hex digits of the reply's SHA-256,
// so a label can never be applied to a different reply. Replies with parser or schema problems are not labelled:
// their problems are listed by the checks.
export const LABELS = {
  // llama3.2:3b, OpenUI with examples (9 clean)
  'llama3.2:3b|openui-examples|r01-one-metric': { sha: 'a244b1cf1773', asked: true },
  'llama3.2:3b|openui-examples|r02-three-metrics': { sha: '95a22bac1604', asked: true, note: 'metrics in a nested Stack, which the parser accepts' },
  'llama3.2:3b|openui-examples|r03-bar-chart': { sha: 'c36c20ddd02e', asked: true },
  'llama3.2:3b|openui-examples|r04-table': { sha: '06bd22363805', asked: true },
  'llama3.2:3b|openui-examples|r05-text-and-table': { sha: 'cfc707e59a81', asked: false, note: 'the requested sentence came before the code; the parser dropped it without an error' },
  'llama3.2:3b|openui-examples|r07-dashboard': { sha: '1a1b8bf2895e', asked: true },
  'llama3.2:3b|openui-examples|r08-wide-table': { sha: '672c1558298f', asked: true },
  'llama3.2:3b|openui-examples|r10-two-charts': { sha: '6685cb02174e', asked: true },
  'llama3.2:3b|openui-examples|r11-text-only': { sha: 'ed20696493b2', asked: true },
  // llama3.2:3b, OpenUI with repair (1 clean)
  'llama3.2:3b|openui-repair|r05-text-and-table': { sha: 'c45461084903', asked: false, note: 'three metrics all labelled "Shipments", company names lost, no sentence' },
  // llama3.2:3b, JSON (12 passed the schema)
  'llama3.2:3b|json|r01-one-metric': { sha: 'a643c454319f', asked: true },
  'llama3.2:3b|json|r02-three-metrics': { sha: '4d882fa92b83', asked: true },
  'llama3.2:3b|json|r03-bar-chart': { sha: 'a1992efef231', asked: true },
  'llama3.2:3b|json|r04-table': { sha: '4dd072060cfa', asked: true },
  'llama3.2:3b|json|r05-text-and-table': { sha: 'c51bb1adf351', asked: true, note: 'the sentence is shown as a Heading' },
  'llama3.2:3b|json|r06-chart-and-table': { sha: '0bab1e2b67b9', asked: true },
  'llama3.2:3b|json|r07-dashboard': { sha: 'c7a361aa6366', asked: false, note: 'added a wrong Text: "Total packages: 3000 + 2543 + 457 = 5000"' },
  'llama3.2:3b|json|r08-wide-table': { sha: 'c931613b0608', asked: true },
  'llama3.2:3b|json|r09-pick-components': { sha: '605d69c58b6f', asked: false, note: 'a Metric "Average Delivery Time" shows 39.7, which is one company\'s value' },
  'llama3.2:3b|json|r10-two-charts': { sha: '78a6c8e1ac62', asked: true },
  'llama3.2:3b|json|r11-text-only': { sha: '57508ce15e0e', asked: true },
  'llama3.2:3b|json|r12-metric-and-text': { sha: 'db0fab58ecde', asked: false, note: 'the requested sentence is missing' },
  // qwen2.5:3b, OpenUI (2 clean), with examples (6 clean), with repair (2 clean)
  'qwen2.5:3b|openui|r02-three-metrics': { sha: '1a8f4741b076', asked: true },
  'qwen2.5:3b|openui|r11-text-only': { sha: '2b3c3c6589c1', asked: true },
  'qwen2.5:3b|openui-examples|r02-three-metrics': { sha: '1868339c2389', asked: true },
  'qwen2.5:3b|openui-examples|r04-table': { sha: '06bd22363805', asked: true },
  'qwen2.5:3b|openui-examples|r09-pick-components': { sha: 'f36b28eb6a14', asked: true },
  'qwen2.5:3b|openui-examples|r10-two-charts': { sha: '52e8d481cf06', asked: false, note: 'both charts titled "Shipment Status": shipped and delivered cannot be told apart' },
  'qwen2.5:3b|openui-examples|r11-text-only': { sha: 'ecb9475150c8', asked: true },
  'qwen2.5:3b|openui-examples|r12-metric-and-text': { sha: '3324f1ef2246', asked: true },
  'qwen2.5:3b|openui-repair|r02-three-metrics': { sha: '1a8f4741b076', asked: true },
  'qwen2.5:3b|openui-repair|r11-text-only': { sha: '2b3c3c6589c1', asked: true },
  // qwen2.5:3b, JSON (12 passed the schema)
  'qwen2.5:3b|json|r01-one-metric': { sha: '6a97f10dd7fa', asked: true },
  'qwen2.5:3b|json|r02-three-metrics': { sha: '6bc5e6b55da1', asked: true },
  'qwen2.5:3b|json|r03-bar-chart': { sha: '915f751f141d', asked: true },
  'qwen2.5:3b|json|r04-table': { sha: 'b7927012a156', asked: true },
  'qwen2.5:3b|json|r05-text-and-table': { sha: 'e3caf59df42c', asked: false, note: 'the "sentence" is a question heading that does not say which company shipped the most' },
  'qwen2.5:3b|json|r06-chart-and-table': { sha: '6aa6290825c3', asked: true },
  'qwen2.5:3b|json|r07-dashboard': { sha: '9826b313ca4c', asked: true },
  'qwen2.5:3b|json|r08-wide-table': { sha: '3d2a777773b6', asked: false, note: 'the last cell holds "199]]}}]}}user\\nCould you also provide…": the model ran on inside a string, and the schema still matched' },
  'qwen2.5:3b|json|r09-pick-components': { sha: '71b4ab037fd2', asked: true },
  'qwen2.5:3b|json|r10-two-charts': { sha: '71c75c5047b8', asked: true, note: 'titles "Shipment" and "Delivery" instead of "Shipped" and "Delivered"' },
  'qwen2.5:3b|json|r11-text-only': { sha: 'a5e8c1e11a65', asked: true },
  'qwen2.5:3b|json|r12-metric-and-text': { sha: 'a743d48fd1eb', asked: true },
};
