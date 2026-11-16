"""Helpers around the Langfuse SDK: build a client, read data back through the public API, peek at the queue."""

import time
from typing import Any

from langfuse import Langfuse
from langfuse.api import LangfuseAPI

from fleet.config import LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

# Field groups of GET /api/public/v2/observations (see the docstring of api.observations.get_many).
ALL_FIELDS = "core,basic,time,io,metadata,model,usage,metrics,trace_context"


def make_client(**kwargs: Any) -> Langfuse:
    """A Langfuse client for the local stack. Keyword arguments override the defaults (base_url, flush settings,
    mask hooks, ...). The SDK keeps one client per public key in a process, so configure it once per process."""
    settings = {
        "public_key": LANGFUSE_PUBLIC_KEY,
        "secret_key": LANGFUSE_SECRET_KEY,
        "base_url": LANGFUSE_BASE_URL,
    }
    settings.update(kwargs)
    return Langfuse(**settings)


def make_reader(timeout: float = 10.0) -> LangfuseAPI:
    """A REST client for reading data back, independent of the tracing client (which may point elsewhere)."""
    return LangfuseAPI(
        base_url=LANGFUSE_BASE_URL, username=LANGFUSE_PUBLIC_KEY, password=LANGFUSE_SECRET_KEY, timeout=timeout
    )


def list_observations(reader: LangfuseAPI, fields: str = ALL_FIELDS, **filters: Any) -> list[Any]:
    """All observations matching the filters, following the cursor."""
    out, cursor = [], None
    while True:
        page = reader.observations.get_many(fields=fields, limit=1000, cursor=cursor, **filters)
        out.extend(page.data)
        cursor = getattr(page.meta, "cursor", None) if page.meta else None
        if not cursor or not page.data:
            return out


def wait_for(fetch, expected: int, timeout: float = 90.0, interval: float = 1.0, settle: float = 0.0):
    """Call fetch() until it returns at least `expected` items or the timeout passes; returns (items, seconds).

    With expected=0 or when fewer items may arrive (data loss), it waits the full timeout, then returns what
    arrived. `settle` keeps polling a little longer after the count is reached, to catch late duplicates.
    """
    start = time.monotonic()
    items = fetch()
    while len(items) < expected and time.monotonic() - start < timeout:
        time.sleep(interval)
        items = fetch()
    if settle and len(items) >= expected:
        time.sleep(settle)
        items = fetch()
    return items, time.monotonic() - start


def langfuse_processor(client: Langfuse):
    """The LangfuseSpanProcessor attached to this client's tracer provider (internal SDK structure, used only to
    read the queue length for the measurements)."""
    from langfuse._client.span_processor import LangfuseSpanProcessor

    provider = client._resources.tracer_provider  # noqa: SLF001
    for p in provider._active_span_processor._span_processors:  # noqa: SLF001
        if isinstance(p, LangfuseSpanProcessor):
            return p
    return None


def queue_length(client: Langfuse) -> int | None:
    """Spans waiting in the OpenTelemetry batch processor's in-memory queue (internal attribute)."""
    p = langfuse_processor(client)
    return len(p._batch_processor._queue) if p else None  # noqa: SLF001


def queue_capacity(client: Langfuse) -> int | None:
    p = langfuse_processor(client)
    return p._batch_processor._max_queue_size if p else None  # noqa: SLF001
