"""Unit-of-Work and Repository ports (pure, typed, no infrastructure).

These abstractions let the kernel/application layer group a set of writes into a single
atomic transaction without depending on SQLAlchemy, psycopg or any driver (LAW-12, rule 10).
Concrete adapters live under ``northstar.adapters.persistence_sqlalchemy`` and implement the
contract below; the kernel only ever sees the port.

Contract (LSP, rule 20): implementations MUST behave as a transactional boundary —

- entering the context manager begins a transaction;
- leaving it without an exception and without an explicit :meth:`UnitOfWorkPort.commit`
  rolls the transaction back (commit is explicit, never implicit);
- leaving it because an exception propagated rolls the transaction back and re-raises;
- :meth:`UnitOfWorkPort.commit` durably persists staged work; :meth:`UnitOfWorkPort.rollback`
  discards it. Both are idempotent within a single unit.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, TypeVar, runtime_checkable

T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class UnitOfWorkPort(Protocol):
    """A transactional boundary around one or more repository operations.

    Used as a context manager. The ``session`` attribute is an opaque handle onto the
    underlying transactional resource; kernel code treats it as ``Any`` and never inspects
    engine-specific types, preserving purity at the boundary.
    """

    session: Any

    def __enter__(self) -> UnitOfWorkPort: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None:
        """Durably persist all work staged in this unit."""
        ...

    def rollback(self) -> None:
        """Discard all work staged in this unit."""
        ...


@runtime_checkable
class RepositoryPort(Protocol[T_co]):
    """A minimal read port for aggregates addressed by opaque identity.

    Kept intentionally small (ISP, rule 20): write-side capabilities compose their own
    role-specific ports. Returns ``None`` when the entity is absent (deny-by-default reads).
    """

    def get(self, entity_id: str) -> T_co | None: ...
