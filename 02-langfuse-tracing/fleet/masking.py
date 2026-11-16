"""Redaction of email addresses and phone numbers, in the two shapes the Langfuse Python SDK accepts.

- `mask_data` is a `mask=` function: called with each input, output and metadata value when the SDK
  sets it on an observation.
- `mask_otel_spans` is a `mask_otel_spans=` hook: called with each OpenTelemetry export batch and
  returns patches for the span attributes that contain a match.
"""

import json
import re
from typing import Any

from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# Phone numbers: an optional +country code, then 8 to 15 digits with optional spaces, dots, dashes or brackets.
PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{6,}\d(?![\w])")

EMAIL_TOKEN = "[EMAIL]"
PHONE_TOKEN = "[PHONE]"


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def redact_text(text: str) -> str:
    text = EMAIL.sub(EMAIL_TOKEN, text)
    return PHONE.sub(lambda m: PHONE_TOKEN if 8 <= _digits(m.group()) <= 15 else m.group(), text)


def redact(value: Any) -> Any:
    """Redact strings anywhere inside JSON-like data (dicts, lists, strings)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def mask_data(*, data: Any, **kwargs: Any) -> Any:
    return redact(data)


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult | None:
    patches = {}
    for identifier, span in params.spans.items():
        replacements = {}
        for key, value in span.attributes.items():
            if not isinstance(value, str):
                continue
            if ".metadata." in key:
                # SDK v5 stores metadata attributes JSON-encoded: decode, redact, encode again.
                try:
                    decoded = json.loads(value)
                except json.JSONDecodeError:
                    decoded = None
                if decoded is not None:
                    new = json.dumps(redact(decoded))
                    if new != value:
                        replacements[key] = new
                    continue
            new = redact_text(value)
            if new != value:
                replacements[key] = new
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches) if patches else None
