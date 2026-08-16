"""SQLAlchemy 2.x persistence adapter (infra allowed here).

Implements the kernel :mod:`northstar.kernel.persistence.ports` behind the boundary:
an engine/session factory that reads ``DATABASE_URL``, a :class:`SqlAlchemyUnitOfWork`
providing transactional semantics, shared declarative metadata and a typed error mapping.
"""

from __future__ import annotations

from .engine import (
    create_engine_from_url,
    create_session_factory,
    resolve_database_url,
)
from .errors import (
    IntegrityViolation,
    OperationalFailure,
    PersistenceError,
    map_persistence_error,
)
from .jobs import (
    JobNotFoundError,
    LeaseNotHeldError,
    SqlAlchemyJobQueue,
    SqlAlchemyScheduler,
)
from .metadata import METADATA_SCHEMA, Base
from .outbox import (
    SqlAlchemyOutbox,
    SqlAlchemyOutboxBacklog,
    SqlAlchemyOutboxRelay,
)
from .runtime_tables import (
    RUNTIME_SCHEMA,
    RUNTIME_TABLES,
    RuntimeTables,
    build_runtime_tables,
    runtime_metadata,
)
from .unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "METADATA_SCHEMA",
    "RUNTIME_SCHEMA",
    "RUNTIME_TABLES",
    "Base",
    "IntegrityViolation",
    "JobNotFoundError",
    "LeaseNotHeldError",
    "OperationalFailure",
    "PersistenceError",
    "RuntimeTables",
    "SqlAlchemyJobQueue",
    "SqlAlchemyOutbox",
    "SqlAlchemyOutboxBacklog",
    "SqlAlchemyOutboxRelay",
    "SqlAlchemyScheduler",
    "SqlAlchemyUnitOfWork",
    "build_runtime_tables",
    "create_engine_from_url",
    "create_session_factory",
    "map_persistence_error",
    "resolve_database_url",
    "runtime_metadata",
]
