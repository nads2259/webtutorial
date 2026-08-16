"""SQLAlchemy Core table for durable saga terminal state (adapter layer, FR-KRN-004).

Infrastructure is allowed here (rule 10). ``saga_state`` records the terminal outcome of a saga run
keyed by ``(organization_id, saga_id)`` so re-executing a saga id is idempotent and durable across
process restarts. Lives in the kernel-owned ``northstar_runtime`` schema on PostgreSQL (created by
migration ``000026`` with FORCED tenant RLS); the builder is parameterised on ``schema`` so portable
tests can materialise the same shape in another schema via ``metadata.create_all``.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

from .metadata import NAMING_CONVENTION

RUNTIME_SCHEMA = "northstar_runtime"

SAGA_STATUSES = ("committed", "compensated")
_SAGA_STATUS_CHECK = "status IN ('committed','compensated')"


def _jsonb() -> JSON:
    """A JSON column that renders as ``jsonb`` on PostgreSQL and portable ``JSON`` elsewhere."""
    return JSON().with_variant(JSONB, "postgresql")


def build_saga_state_table(metadata: MetaData, *, schema: str | None = RUNTIME_SCHEMA) -> Table:
    """Define the ``saga_state`` table on ``metadata`` in ``schema`` (mirrors migration 000026)."""
    saga_state = Table(
        "saga_state",
        metadata,
        Column("organization_id", String, primary_key=True, nullable=False),
        Column("saga_id", String, primary_key=True, nullable=False),
        Column(
            "status",
            String,
            CheckConstraint(_SAGA_STATUS_CHECK, name="saga_state_status_check"),
            nullable=False,
        ),
        Column("completed_steps", _jsonb(), nullable=False),
        Column("compensated_steps", _jsonb(), nullable=False),
        Column("error", String, nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("saga_state_org_idx", saga_state.c.organization_id)
    return saga_state


saga_metadata = MetaData(naming_convention=NAMING_CONVENTION)
"""Dedicated metadata for ``northstar_runtime.saga_state`` (kept separate from ``Base``)."""

SAGA_STATE_TABLE = build_saga_state_table(saga_metadata)
"""Default ``saga_state`` table bound to the ``northstar_runtime`` schema (PostgreSQL)."""

__all__ = [
    "RUNTIME_SCHEMA",
    "SAGA_STATE_TABLE",
    "SAGA_STATUSES",
    "build_saga_state_table",
    "saga_metadata",
]
