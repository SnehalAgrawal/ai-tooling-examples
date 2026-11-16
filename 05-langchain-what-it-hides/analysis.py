"""Pure logic, no network: diffing two request bodies, grading an extraction, summary statistics."""

from __future__ import annotations

import math
from typing import Any

MISSING = "<absent>"


def json_diff(a: Any, b: Any, path: str = "") -> list[dict[str, Any]]:
    """Every difference between two JSON values, as ``{path, kind, a, b}``.

    ``kind`` is ``only_in_a`` (a key or list item ``b`` lacks), ``only_in_b``, or ``changed``
    (both have it, with different values, or a string against a number). Numbers compare by value,
    so ``0`` and ``0.0`` are equal, as they are in JSON.
    Paths look like ``messages[1].content`` or ``options.temperature``.
    """
    out: list[dict[str, Any]] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            p = f"{path}.{k}" if path else str(k)
            if k not in b:
                out.append({"path": p, "kind": "only_in_a", "a": a[k], "b": MISSING})
            elif k not in a:
                out.append({"path": p, "kind": "only_in_b", "a": MISSING, "b": b[k]})
            else:
                out.extend(json_diff(a[k], b[k], p))
        return out
    if isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(b):
                out.append({"path": p, "kind": "only_in_a", "a": a[i], "b": MISSING})
            elif i >= len(a):
                out.append({"path": p, "kind": "only_in_b", "a": MISSING, "b": b[i]})
            else:
                out.extend(json_diff(a[i], b[i], p))
        return out
    if not _same_scalar(a, b):
        out.append({"path": path, "kind": "changed", "a": a, "b": b})
    return out


def _same_scalar(a: Any, b: Any) -> bool:
    """Equal JSON scalars. 0 and 0.0 are the same JSON number; true and 1 are not (bool is an int in Python)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def short(value: Any, limit: int = 70) -> str:
    """A one-line rendering of a JSON value for a printed table."""
    import json

    s = value if value == MISSING else json.dumps(value, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def format_diff(diff: list[dict[str, Any]], a_name: str = "LangChain", b_name: str = "plain") -> list[str]:
    if not diff:
        return ["  (identical)"]
    lines = []
    for d in diff:
        if d["kind"] == "only_in_a":
            lines.append(f"  + {d['path']} = {short(d['a'])}   (only {a_name})")
        elif d["kind"] == "only_in_b":
            lines.append(f"  - {d['path']} = {short(d['b'])}   (only {b_name})")
        else:
            lines.append(f"  ~ {d['path']}: {a_name} {short(d['a'])}  vs  {b_name} {short(d['b'])}")
    return lines


# --------------------------------------------------------------------------- grading


def normalise(field: str, value: Any) -> Any:
    """Normalise a field value before comparing: IDs and cities ignore case and surrounding space,
    enums compare by value, ``delay_hours`` accepts an int-valued float or numeric string."""
    if value is None:
        return None
    if hasattr(value, "value"):  # an Enum member
        value = value.value
    if field in ("package_id", "city", "reason"):
        return str(value).strip().casefold()
    if field == "delay_hours":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and float(value).is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                f = float(value)
                return int(f) if f.is_integer() else f
            except ValueError:
                return value
    return value


def grade(parsed: Any, expected: dict[str, Any]) -> dict[str, Any]:
    """Grade one extraction field by field.

    ``parsed`` is a Pydantic object, a dict, or ``None`` (nothing parsed). Returns
    ``{"fields": {name: bool}, "correct": n, "total": n, "all_correct": bool}``.
    """
    if parsed is None:
        data: dict[str, Any] = {}
    elif hasattr(parsed, "model_dump"):
        data = parsed.model_dump()
    elif isinstance(parsed, dict):
        data = parsed
    else:
        data = {}
    fields = {}
    for name, want in expected.items():
        if name not in data:
            fields[name] = False
            continue
        fields[name] = normalise(name, data[name]) == normalise(name, want)
    correct = sum(fields.values())
    return {"fields": fields, "correct": correct, "total": len(expected), "all_correct": correct == len(expected)}


def schema_valid(model_cls: type, data: Any) -> tuple[bool, str | None]:
    """Whether ``data`` validates against a Pydantic model, and the first error if not."""
    from pydantic import ValidationError

    if data is None:
        return False, "nothing parsed"
    if isinstance(data, model_cls):
        return True, None
    try:
        model_cls.model_validate(data)
        return True, None
    except ValidationError as e:
        err = e.errors()[0]
        return False, f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"


# --------------------------------------------------------------------------- statistics


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in 0..100), the same as numpy's default."""
    if not values:
        return math.nan
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
