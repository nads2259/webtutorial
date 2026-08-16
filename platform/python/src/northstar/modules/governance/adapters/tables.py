"""SQLAlchemy Core tables for the governance data owner (schema ``northstar_governance``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000020_governance`` exactly
and live in the ``northstar_governance`` schema on PostgreSQL. Every tenant-scoped table carries an
explicit ``organization_id`` — the RLS tenant column (defense-in-depth, FR-POL-004) and the
predicate every repository query includes. The builder is parameterised on ``schema`` so portable
tests can materialise the same shape in another schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

GOVERNANCE_SCHEMA = "northstar_governance"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class GovernanceTables:
    """Core tables backing immutable decision records and time-bounded control exceptions."""

    decision: Table
    exception: Table


def build_governance_tables(
    metadata: MetaData, *, schema: str | None = GOVERNANCE_SCHEMA
) -> GovernanceTables:
    """Define the governance tables on ``metadata`` in ``schema`` (mirrors migration 000020)."""
    decision = Table(
        "governance_decision",
        metadata,
        Column("decision_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("title", String, nullable=False),
        Column("status", String, nullable=False),
        Column("rationale", String, nullable=False),
        Column("decider", _jsonb(), nullable=False),
        Column("links", _jsonb(), nullable=False),
        Column("supersedes", String, nullable=True),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("governance_decision_org_idx", decision.c.organization_id)
    Index("governance_decision_supersedes_idx", decision.c.organization_id, decision.c.supersedes)

    exception = Table(
        "governance_exception",
        metadata,
        Column("exception_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("control", String, nullable=False),
        Column("subject", String, nullable=False),
        Column("approver", _jsonb(), nullable=False),
        Column("granted_by", _jsonb(), nullable=False),
        Column("rationale", String, nullable=False),
        Column("status", String, nullable=False),
        Column("expiry", DateTime(timezone=True), nullable=False),
        Column("granted_at", DateTime(timezone=True), nullable=False),
        Column("revoked_by", _jsonb(), nullable=True),
        Column("revoked_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("governance_exception_org_idx", exception.c.organization_id)
    Index(
        "governance_exception_control_idx",
        exception.c.organization_id,
        exception.c.control,
    )

    return GovernanceTables(decision=decision, exception=exception)
