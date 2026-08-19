"""SQLAlchemy Core table for the durable audit sink (schema ``northstar_audit``).

Mirrors migration ``000028_audit``. The kernel audit trail is a process-global, append-only evidence
log (LAW-14): every meaningful action leaves a tamper-evident record with a ``record_sha256`` over its
canonical content. It is intentionally NOT tenant-partitioned (RLS) — the tenant, when relevant, is
carried in the record's ``resource`` — so the sink can hold cross-tenant platform/system evidence.
"""

from __future__ import annotations

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

AUDIT_SCHEMA = "northstar_audit"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


def build_audit_table(metadata: MetaData, *, schema: str | None = AUDIT_SCHEMA) -> Table:
    """Define the ``audit_record`` table on ``metadata`` (mirrors migration 000028)."""
    audit_record = Table(
        "audit_record",
        metadata,
        Column("evidence_id", String, primary_key=True),
        Column("event_type", String, nullable=False),
        Column("occurred_at", String, nullable=False),
        Column("actor_type", String, nullable=False),
        Column("actor_id", String, nullable=False),
        Column("actor_delegated_by", String, nullable=True),
        Column("action", String, nullable=False),
        Column("outcome", String, nullable=False),
        Column("correlation_id", String, nullable=False),
        Column("resource_type", String, nullable=True),
        Column("resource_id", String, nullable=True),
        Column("decision_ref", String, nullable=True),
        Column("reason_codes", _jsonb(), nullable=False),
        Column("record_sha256", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("audit_record_correlation_idx", audit_record.c.correlation_id)
    Index("audit_record_action_idx", audit_record.c.action)
    Index("audit_record_actor_idx", audit_record.c.actor_id)
    Index("audit_record_occurred_idx", audit_record.c.occurred_at)
    return audit_record
