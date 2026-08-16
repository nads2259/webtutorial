"""SQLAlchemy Core table definitions for the runtime outbox + job queue (adapter layer).

Infrastructure is allowed here (rule 10). These tables mirror
``spec/reference/one-touch/db/migrations/000002_runtime_outbox_jobs.sql`` exactly (columns,
constraints, indexes) and live in the ``northstar_runtime`` schema on PostgreSQL. The builder is
parameterised on ``schema`` so portable unit tests can materialise the same shape in SQLite's
default schema (``schema=None``) via ``metadata.create_all``; on PostgreSQL the tables are created
by migration ``000002`` and the adapter binds to them here for typed, parameterised access.

``jsonb`` columns use a PostgreSQL variant so they map to ``JSONB`` on PostgreSQL and portable
``JSON`` elsewhere; timestamp columns are timezone-aware; the callers always supply values
explicitly so no server-side defaults are required for portable operation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from .metadata import NAMING_CONVENTION

RUNTIME_SCHEMA = "northstar_runtime"

JOB_STATUSES = ("ready", "leased", "succeeded", "failed", "dead")
_JOB_STATUS_CHECK = "status IN ('ready','leased','succeeded','failed','dead')"


def _jsonb() -> JSON:
    """A JSON column that renders as ``jsonb`` on PostgreSQL and portable ``JSON`` elsewhere."""
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class RuntimeTables:
    """The pair of Core tables backing the outbox relay and job queue."""

    outbox_event: Table
    job: Table


def build_runtime_tables(
    metadata: MetaData, *, schema: str | None = RUNTIME_SCHEMA
) -> RuntimeTables:
    """Define ``outbox_event`` and ``job`` on ``metadata`` in ``schema`` (mirrors reference SQL)."""
    outbox_event = Table(
        "outbox_event",
        metadata,
        Column("event_id", Uuid(as_uuid=False), primary_key=True),
        Column("event_type", String, nullable=False),
        Column("event_version", String, nullable=False),
        Column("aggregate_type", String, nullable=False),
        Column("aggregate_id", String, nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("payload", _jsonb(), nullable=False),
        Column("metadata", _jsonb(), nullable=False),
        Column("dispatched_at", DateTime(timezone=True), nullable=True),
        Column("attempt_count", Integer, nullable=False, default=0),
        Column("next_attempt_at", DateTime(timezone=True), nullable=False),
        Column("last_error", String, nullable=True),
        schema=schema,
    )
    Index(
        "outbox_ready_idx",
        outbox_event.c.next_attempt_at,
        outbox_event.c.occurred_at,
        postgresql_where=outbox_event.c.dispatched_at.is_(None),
    )

    job = Table(
        "job",
        metadata,
        Column("job_id", Uuid(as_uuid=False), primary_key=True),
        Column("job_type", String, nullable=False),
        Column("job_version", String, nullable=False),
        Column("queue", String, nullable=False),
        Column("idempotency_key", String, nullable=False),
        Column("payload", _jsonb(), nullable=False),
        Column(
            "status",
            String,
            CheckConstraint(_JOB_STATUS_CHECK, name="job_status_check"),
            nullable=False,
        ),
        Column("available_at", DateTime(timezone=True), nullable=False),
        Column("lease_owner", String, nullable=True),
        Column("lease_expires_at", DateTime(timezone=True), nullable=True),
        Column("attempt_count", Integer, nullable=False, default=0),
        Column("max_attempts", Integer, nullable=False, default=10),
        Column("last_error", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("attempt_count >= 0", name="job_attempt_count_nonneg"),
        schema=schema,
    )
    Index(
        "job_ready_idx", job.c.queue, job.c.available_at, postgresql_where=job.c.status == "ready"
    )
    # Idempotency: one logical job per (job_type, idempotency_key).
    Index("uq_job_type_idempotency_key", job.c.job_type, job.c.idempotency_key, unique=True)

    return RuntimeTables(outbox_event=outbox_event, job=job)


runtime_metadata = MetaData(naming_convention=NAMING_CONVENTION)
"""Dedicated metadata for the ``northstar_runtime`` tables (kept separate from ``Base``)."""

RUNTIME_TABLES = build_runtime_tables(runtime_metadata)
"""Default table set bound to the ``northstar_runtime`` schema (PostgreSQL)."""

__all__ = [
    "JOB_STATUSES",
    "RUNTIME_SCHEMA",
    "RUNTIME_TABLES",
    "RuntimeTables",
    "build_runtime_tables",
    "runtime_metadata",
]
