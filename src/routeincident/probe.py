"""Bounded TCP connect-time probes.

This measures socket connect completion from the probe host. It is NOT an
exchange feed latency measurement or a sub-microsecond NIC benchmark.
"""
from __future__ import annotations

import math
import socket
import statistics
import time


def _quantile(samples: list[float], q: float) -> float | None:
    if not samples:
        return None
    vals = sorted(samples)
    index = (len(vals) - 1) * q
    left, right = math.floor(index), math.ceil(index)
    return round(vals[left] + (vals[right] - vals[left]) * (index - left), 3)


def tcp_probe(name: str, host: str, port: int, count: int = 5,
              timeout_s: float = 1.0, interval_s: float = 0.2) -> dict:
    if not isinstance(count, int) or not 1 <= count <= 50:
        raise ValueError("count must be between 1 and 50")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid TCP port")
    if not (0.05 <= timeout_s <= 10 and 0 <= interval_s <= 10):
        raise ValueError("timeout or interval out of range")
    samples: list[float] = []
    failures = 0
    errors: list[str] = []
    for n in range(count):
        begin = time.perf_counter_ns()
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                pass
            samples.append(round((time.perf_counter_ns() - begin) / 1_000_000, 3))
        except OSError as exc:
            failures += 1
            if len(errors) < 3:
                errors.append(type(exc).__name__)
        if n + 1 < count and interval_s:
            time.sleep(interval_s)
    return {
        "name": name, "target": f"{host}:{port}", "attempts": count,
        "successes": len(samples), "failures": failures,
        "loss_pct": round(failures / count * 100, 2),
        "connect_failure_pct": round(failures / count * 100, 2),
        "p50_ms": _quantile(samples, 0.50),
        "p95_ms": _quantile(samples, 0.95),
        "p99_ms": _quantile(samples, 0.99),
        "min_ms": round(min(samples), 3) if samples else None,
        "max_ms": round(max(samples), 3) if samples else None,
        "errors": errors,
    }
