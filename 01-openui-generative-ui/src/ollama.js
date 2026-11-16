// Ollama's native chat API, streamed, with the settings every run uses. Returns the reply and its timings.
export const OLLAMA_URL = process.env.OLLAMA_URL ?? 'http://localhost:11434';
export const OPTIONS = { temperature: 0, seed: 42, num_ctx: 8192 };

/** Unload a model, so each block of requests starts from the same state. */
export async function unload(model) {
  await fetch(`${OLLAMA_URL}/api/generate`, { method: 'POST', body: JSON.stringify({ model, keep_alive: 0 }) });
}

/**
 * Stream one chat request. `onText(fullTextSoFar, msSinceStart)` runs after every chunk.
 * `format` is a JSON schema for Ollama's constrained output, or undefined for free text.
 */
export async function chat(model, messages, { format, onText } = {}) {
  const started = performance.now();
  const res = await fetch(`${OLLAMA_URL}/api/chat`, {
    method: 'POST',
    body: JSON.stringify({ model, messages, stream: true, options: OPTIONS, format }),
  });
  if (!res.ok) throw new Error(`Ollama returned ${res.status}: ${await res.text()}`);
  let text = '';
  let firstTokenMs = null;
  let final = null;
  let buffered = '';
  const decoder = new TextDecoder();
  for await (const bytes of res.body) {
    buffered += decoder.decode(bytes, { stream: true });
    let newline;
    while ((newline = buffered.indexOf('\n')) >= 0) {
      const line = buffered.slice(0, newline).trim();
      buffered = buffered.slice(newline + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.error) throw new Error(`Ollama: ${event.error}`);
      const piece = event.message?.content ?? '';
      if (piece) {
        firstTokenMs ??= performance.now() - started;
        text += piece;
        onText?.(text, performance.now() - started);
      }
      if (event.done) final = event;
    }
  }
  return {
    text,
    firstTokenMs,
    totalMs: performance.now() - started,
    outputTokens: final?.eval_count,
    promptTokens: final?.prompt_eval_count,
    doneReason: final?.done_reason,
  };
}
