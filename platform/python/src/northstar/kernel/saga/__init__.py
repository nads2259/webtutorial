"""Kernel saga/compensation runtime (durable workflow state + compensation, FR-KRN-004).

A small, pure (stdlib-only, rule 10) coordinator that runs an ordered list of steps, each paired
with a compensating action, so a partially-applied multi-step operation is deterministically rolled
back on failure — the compensations for already-applied steps run in REVERSE order, leaving no
partial effect. Terminal outcomes are recorded through a :class:`SagaStateStorePort` keyed by
``(organization_id, saga_id)`` so re-running the same saga id is idempotent (a no-op returning the
recorded outcome). An in-memory store ships here; a durable PostgreSQL adapter lives behind the port
in ``northstar.adapters.persistence_sqlalchemy`` (forced tenant RLS).
"""

from __future__ import annotations

from .ports import InMemorySagaStateStore, SagaRecord, SagaStateStorePort, SagaStatus
from .runtime import SagaCoordinator, SagaOutcome, SagaStep

__all__ = [
    "InMemorySagaStateStore",
    "SagaCoordinator",
    "SagaOutcome",
    "SagaRecord",
    "SagaStateStorePort",
    "SagaStatus",
    "SagaStep",
]
