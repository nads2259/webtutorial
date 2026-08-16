"""Pure vector math for the exact semantic baseline (rule 10/21).

Deterministic, dependency-light helpers reused by the local reference embedding adapter and by the
exact-search recall/precision baseline tests. No numpy: retrieval's exact baseline runs on small
gold sets, so a pure-Python implementation keeps the domain infrastructure-free and reproducible.
Vectors are immutable ``tuple[float, ...]``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Vector = tuple[float, ...]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the dot product of two equal-length vectors."""
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def l2_norm(a: Sequence[float]) -> float:
    """Return the Euclidean (L2) magnitude of ``a``."""
    return math.sqrt(math.fsum(x * x for x in a))


def normalize(a: Sequence[float]) -> Vector:
    """Return ``a`` scaled to unit length; a zero vector is returned unchanged (as zeros).

    Unit-normalising makes L2 distance and cosine distance rank-equivalent, so the exact
    baseline is stable regardless of which distance metric the profile records.
    """
    norm = l2_norm(a)
    if norm == 0.0:
        return tuple(float(x) for x in a)
    return tuple(float(x) / norm for x in a)


def l2_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the Euclidean distance between two equal-length vectors (pgvector ``<->``)."""
    return math.sqrt(math.fsum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Return cosine similarity in ``[-1, 1]``; ``0.0`` when either vector is all zeros."""
    denom = l2_norm(a) * l2_norm(b)
    if denom == 0.0:
        return 0.0
    return dot(a, b) / denom
