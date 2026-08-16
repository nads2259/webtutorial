"""SQLAlchemy Core tables for the support data owner (schema ``northstar_support``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000015`` exactly and live in the
``northstar_support`` schema. Every table is tenant-scoped by an explicit ``organization_id`` column
— the RLS tenant column (defense-in-depth, rule 50) and the predicate every repository query
includes. The builder is parameterised on ``schema`` so portable tests can materialise the same
shape in a throwaway schema.

Internal messages (``visibility = 'internal'``) are stored alongside requester-visible ones but are
excluded from the MINIMIZED staff projection (FR-SUP-003). ``support_access_grant`` is the audited,
time-bounded elevated-access grant and ``support_access_log`` is the tamper-evident record of every
minimized/elevated read (including refused broad reads).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

SUPPORT_SCHEMA = "northstar_support"

SUPPORT_TENANT_TABLES: tuple[str, ...] = (
    "support_case",
    "support_message",
    "support_access_grant",
    "support_access_log",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class SupportTables:
    """The support module tables plus the schema they live in."""

    schema: str
    support_case: Table
    support_message: Table
    support_access_grant: Table
    support_access_log: Table


def build_support_tables(
    metadata: MetaData, *, schema: str | None = SUPPORT_SCHEMA
) -> SupportTables:
    """Define the support tables on ``metadata`` in ``schema`` (mirrors migration 000015)."""
    support_case = Table(
        "support_case",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("case_id", String, primary_key=True),
        Column("requester_id", String, nullable=False),
        Column("assignee_id", String, nullable=True),
        Column("status", String, nullable=False),
        Column("priority", String, nullable=False),
        Column("category", String, nullable=False),
        Column("subject", String, nullable=True),
        Column("audit_scope", String, nullable=False),
        Column("retention_policy", String, nullable=True),
        Column("related_resources", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("support_case_org_idx", support_case.c.organization_id)
    Index("support_case_requester_idx", support_case.c.organization_id, support_case.c.requester_id)

    support_message = Table(
        "support_message",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("message_id", String, primary_key=True),
        Column("case_id", String, nullable=False),
        Column("author_type", String, nullable=False),
        Column("body_ref", String, nullable=False),
        Column("body", Text, nullable=False),
        Column("visibility", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("support_message_org_idx", support_message.c.organization_id)
    Index("support_message_case_idx", support_message.c.organization_id, support_message.c.case_id)

    support_access_grant = Table(
        "support_access_grant",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("grant_id", String, primary_key=True),
        Column("case_id", String, nullable=False),
        Column("staff_id", String, nullable=False),
        Column("granted_by", String, nullable=False),
        Column("reason", String, nullable=False),
        Column("scope", String, nullable=False),
        Column("starts_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("revoked", Boolean, nullable=False),
        Column("revoked_at", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("support_access_grant_org_idx", support_access_grant.c.organization_id)
    Index(
        "support_access_grant_lookup_idx",
        support_access_grant.c.organization_id,
        support_access_grant.c.case_id,
        support_access_grant.c.staff_id,
    )

    support_access_log = Table(
        "support_access_log",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("log_id", String, primary_key=True),
        Column("case_id", String, nullable=False),
        Column("staff_id", String, nullable=False),
        Column("scope", String, nullable=False),
        Column("decision", String, nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("support_access_log_org_idx", support_access_log.c.organization_id)
    Index(
        "support_access_log_case_idx",
        support_access_log.c.organization_id,
        support_access_log.c.case_id,
    )

    return SupportTables(
        schema=schema or SUPPORT_SCHEMA,
        support_case=support_case,
        support_message=support_message,
        support_access_grant=support_access_grant,
        support_access_log=support_access_log,
    )
