"""Deterministic latency measurement + percentile math for the reference load profile.

Infra-free (stdlib only): the SLO/latency reasoning lives here so it is unit-testable without a
database, HTTP server or clock backend. The harness measures the wall-clock latency of a supplied
callable over a fixed iteration count (with optional warm-up to stabilise caches/JIT-free Python),
then computes the p50/p95/p99 with the **nearest-rank** method so a small deterministic sample
yields a stable percentile (no interpolation flakiness). The read/write p95 budgets encode the
commercial reference profile from ``spec/docs/18`` §10 (reads < 300 ms, writes < 700 ms, excluding
declared third-party/provider latency).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

READ_P95_BUDGET_MS = 300.0
WRITE_P95_BUDGET_MS = 700.0


def percentile(samples: Sequence[float], p: float) -> float:
    """Return the ``p``-th percentile of ``samples`` using the nearest-rank method.

    Nearest-rank (rather than linear interpolation) keeps a small, deterministic sample's
    percentile stable and reproducible. ``p`` is a percentage in ``(0, 100]``.
    """
    if not samples:
        raise ValueError("cannot take a percentile of an empty sample")
    if not 0.0 < p <= 100.0:
        raise ValueError("percentile p must be in the interval (0, 100]")
    ordered = sorted(samples)
    rank = math.ceil((p / 100.0) * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


@dataclass(frozen=True, slots=True)
class LatencyMeasurement:
    """Summary statistics for one measured operation (all times in milliseconds)."""

    operation: str
    iterations: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    samples_ms: tuple[float, ...]

    def within(self, budget_ms: float) -> bool:
        """True iff the measured p95 is at or below ``budget_ms``."""
        return self.p95_ms <= budget_ms


def summarize(operation: str, samples_ms: Sequence[float]) -> LatencyMeasurement:
    """Build a :class:`LatencyMeasurement` from raw per-iteration latencies (ms)."""
    if not samples_ms:
        raise ValueError("cannot summarize an empty latency sample")
    return LatencyMeasurement(
        operation=operation,
        iterations=len(samples_ms),
        p50_ms=percentile(samples_ms, 50),
        p95_ms=percentile(samples_ms, 95),
        p99_ms=percentile(samples_ms, 99),
        max_ms=max(samples_ms),
        samples_ms=tuple(samples_ms),
    )


def measure(
    operation: str,
    call: Callable[[], object],
    *,
    iterations: int,
    warmup: int = 0,
) -> LatencyMeasurement:
    """Run ``call`` ``iterations`` times (after ``warmup`` unrecorded runs) and summarise latency.

    Uses :func:`time.perf_counter` (monotonic, high-resolution). The callable's return value is
    ignored; raise inside ``call`` to fail the harness. Purely local — the caller is responsible
    for excluding declared third-party/provider work from ``call``.
    """
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if warmup < 0:
        raise ValueError("warmup must not be negative")
    for _ in range(warmup):
        call()
    samples_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        call()
        samples_ms.append((time.perf_counter() - start) * 1000.0)
    return summarize(operation, samples_ms)
