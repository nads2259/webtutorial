"""Pure embedding rebuild/cutover selection logic (FR-RET-004, rule 10/21).

Infrastructure-free rules that make an embedding rebuild-and-cutover safe: which single profile
version ``retrieval.search`` serves at any instant, and the guarantee that a result set is never a
mix of two profile versions. The atomic *storage* flip lives in the adapter (a single SQL
statement); this module owns the deterministic *decision* so it is unit-testable without a database.

Rule (deny-by-default, no downtime): search serves the DB-``active`` profile when it is one this
process can embed for; otherwise it falls back to the caller-provided default profile. It never
selects a profile it cannot embed for (which would silently return zero results — a downtime
window). Selecting exactly one ref guarantees no old+new mix within a single query.
"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import RetrievalInvariantViolation
from .model import EmbeddingProfileRef


def select_serving_profile(
    *,
    active: EmbeddingProfileRef | None,
    available: Iterable[EmbeddingProfileRef],
    fallback: EmbeddingProfileRef,
) -> EmbeddingProfileRef:
    """Return the ONE profile version search must serve.

    ``active`` is the DB cutover pointer (``None`` before any activation); ``available`` are the
    profile versions this process can embed a query under; ``fallback`` is the default used when no
    active pointer is set (or it points at a version this process cannot serve). The returned ref is
    always in ``available`` — a query is never dispatched under a profile whose query-embedding is
    absent (which would return zero hits and break the no-downtime guarantee).
    """
    available_set = set(available)
    if fallback not in available_set:
        raise RetrievalInvariantViolation(
            "the fallback embedding profile must be available to serve queries",
            code="retrieval.cutover.fallback",
        )
    if active is not None and active in available_set:
        return active
    return fallback


def single_served_profile(refs: Iterable[EmbeddingProfileRef]) -> EmbeddingProfileRef:
    """Return the sole profile ref present in ``refs`` or raise if the set mixes versions.

    Used to assert the no-mix invariant: every embedding that contributed to one result set must
    belong to a single profile version (FR-RET-004). Raises on an empty or mixed set.
    """
    distinct = set(refs)
    if len(distinct) != 1:
        raise RetrievalInvariantViolation(
            f"a result set must draw from exactly one embedding profile, saw {len(distinct)}",
            code="retrieval.cutover.mixed",
        )
    return next(iter(distinct))
