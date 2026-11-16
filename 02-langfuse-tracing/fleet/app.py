"""The fleet-operations assistant: retrieve snippets, ask the model, return the answer.

`answer_plain` is the app with no tracing code at all. `answer_traced` is the same steps wrapped in
Langfuse observations: a root span, a retriever observation, and a generation.
"""

from dataclasses import dataclass

from fleet.retrieval import retrieve

SYSTEM_PROMPT = (
    "You are the operations assistant of a delivery fleet. Answer the question in one or two sentences, "
    "using only the context. If the context does not contain the answer, say you don't know."
)


@dataclass
class Answer:
    question: str
    text: str
    sources: list[str]
    input_tokens: int | None
    output_tokens: int | None
    model_raw: dict
    trace_id: str | None = None


def build_messages(question: str, snippets: list[dict[str, str]]) -> list[dict[str, str]]:
    context = "\n".join(f"[{s['id']}] {s['text']}" for s in snippets) or "(no matching snippets)"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nContext:\n{context}"},
    ]


def answer_plain(question: str, model, k: int = 2) -> Answer:
    snippets = retrieve(question, k)
    reply = model.chat(build_messages(question, snippets))
    return Answer(
        question, reply.content, [s["id"] for s in snippets], reply.input_tokens, reply.output_tokens, reply.raw
    )


def answer_traced(question: str, model, langfuse, k: int = 2, record_usage: bool = True) -> Answer:
    """The same steps as answer_plain, recorded as one Langfuse trace.

    record_usage=False leaves usage_details out of the generation, to see what Langfuse stores without it.
    """
    with langfuse.start_as_current_observation(
        name="fleet-question", as_type="span", input={"question": question}
    ) as root:
        with langfuse.start_as_current_observation(
            name="retrieve", as_type="retriever", input={"question": question, "k": k}
        ) as step:
            snippets = retrieve(question, k)
            step.update(output=[{"id": s["id"], "title": s["title"]} for s in snippets])

        messages = build_messages(question, snippets)
        with langfuse.start_as_current_observation(
            name="generate",
            as_type="generation",
            model=model.model,
            model_parameters=model.parameters,
            input=messages,
        ) as gen:
            reply = model.chat(messages)
            update = {"output": reply.content}
            if record_usage and reply.input_tokens is not None and reply.output_tokens is not None:
                update["usage_details"] = {"input": reply.input_tokens, "output": reply.output_tokens}
            if reply.raw:
                update["metadata"] = {
                    k: reply.raw[k]
                    for k in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")
                    if k in reply.raw
                }
            gen.update(**update)

        sources = [s["id"] for s in snippets]
        root.update(output={"answer": reply.content, "sources": sources})
        trace_id = langfuse.get_current_trace_id()

    return Answer(question, reply.content, sources, reply.input_tokens, reply.output_tokens, reply.raw, trace_id)
