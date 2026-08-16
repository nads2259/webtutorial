"""Reciprocal-rank fusion (RRF) — the pure hybrid fusion strategy (FR-RET-002, docs/06 §7.6).

Fuses several independently-ranked candidate lists (here: lexical FTS and exact vector search)
into one ranking without needing the component scores to be on a comparable scale. Each candidate
key accrues ``1 / (k + rank)`` from every list it appears in (``rank`` is 1-based); the fused list
is ordered by descending total, ties broken by key so the result is deterministic.

Pure and stdlib-only (rule 10). ``k`` defaults to 60, the value from the original Cormack et al.
RRF paper, and is injectable so fusion behaviour is tunable without code changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedRank:
    """A fused candidate: its ``key`` plus the combined RRF ``score`` (higher is better)."""

    key: str
    score: float


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = DEFAULT_RRF_K
) -> list[FusedRank]:
    """Fuse ranked candidate-key lists into one deterministic ranking (highest score first).

    ``rankings`` is a sequence of component result lists, each already ordered best-first and
    containing stable candidate keys (e.g. chunk ids). A key absent from a list simply contributes
    nothing from that list. ``k`` must be positive.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [FusedRank(key=key, score=score) for key, score in ordered]
