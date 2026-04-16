from __future__ import annotations


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(int(len(ordered) * 0.95) - 1, 0)
    return float(ordered[idx])


def avg(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))

