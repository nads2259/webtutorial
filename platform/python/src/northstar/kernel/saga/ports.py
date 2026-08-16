"""Saga persistence port + persisted state value object (durable workflow state, FR-KRN-004).

The coordinator records a saga's terminal outcome through :class:`SagaStateStorePort` so that
re-executing the same ``saga_id`` is idempotent. The port is intentionally tiny (ISP, rule 20):
``get`` (deny-by-default read — ``None`` when absent) and ``put`` (upsert the terminal record).
Records are tenant-scoped by ``organization_id``; concrete adapters enforce tenant isolation (the
PostgreSQL adapter under forced RLS). The in-memory store here is pure and used by unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class SagaStatus(Enum):
    """The terminal status of a saga run.

    ``COMMITTED`` — every step applied and the saga committed. ``COMPENSATED`` — a step failed and
    the already-applied steps were compensated in reverse (no partial effect). Both are terminal.
    """

    COMMITTED = "committed"
    COMPENSATED = "compensated"


@dataclass(frozen=True, slots=True)
class SagaRecord:
    """The durable, tenant-scoped terminal state of a saga run (what the store persists)."""

    organization_id: str
    saga_id: str
    status: SagaStatus
    completed_steps: tuple[str, ...]
    compensated_steps: tuple[str, ...]
    error: str | None = None


@runtime_checkable
class SagaStateStorePort(Protocol):
    """A durable store of saga terminal outcomes, tenant-scoped by ``organization_id``."""

    def get(self, *, organization_id: str, saga_id: str) -> SagaRecord | None: ...

    def put(self, record: SagaRecord) -> None: ...


class InMemorySagaStateStore(SagaStateStorePort):
    """A pure, tenant-scoped in-memory :class:`SagaStateStorePort` for tests and defaults."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], SagaRecord] = {}

    def get(self, *, organization_id: str, saga_id: str) -> SagaRecord | None:
        return self._records.get((organization_id, saga_id))

    def put(self, record: SagaRecord) -> None:
        self._records[(record.organization_id, record.saga_id)] = record


__all__ = [
    "InMemorySagaStateStore",
    "SagaRecord",
    "SagaStateStorePort",
    "SagaStatus",
]
