"""Summary statistics for timings, with no dependencies."""

import math


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (the same method as numpy's default), p in [0, 100]."""
    if not values:
        raise ValueError("no values")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "median": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "mean": sum(values) / len(values),
    }
