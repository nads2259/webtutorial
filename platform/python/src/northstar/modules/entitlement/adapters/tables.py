"""SQLAlchemy Core table for the entitlement data owner (schema ``northstar_entitlement``).

Infrastructure is allowed here (rule 10). Mirrors migration ``000004_org_entitlement`` exactly.
``organization_id`` is the RLS tenant column for tenant-scoped grants (FR-POL-004); origin is stored
as a stable origin *type*, never a plan/payment name (ARCH-019).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

ENTITLEMENT_SCHEMA = "northstar_entitlement"


@dataclass(frozen=True)
class EntitlementTables:
    """Core table backing entitlement grants."""

    grant: Table


def build_entitlement_tables(
    metadata: MetaData, *, schema: str | None = ENTITLEMENT_SCHEMA
) -> EntitlementTables:
    """Define the entitlement grant table on ``metadata`` (mirrors migration 000004)."""
    grant = Table(
        "entitlement_grant",
        metadata,
        Column("grant_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("organization_id", String, nullable=True),
        Column("capability", String, nullable=False),
        Column("origin", String, nullable=False),
        Column("starts_at", DateTime(timezone=True), nullable=False),
        Column("ends_at", DateTime(timezone=True), nullable=True),
        Column("quota_limit", Integer, nullable=True),
        Column("quota_used", Integer, nullable=False, default=0),
        Column("quota_disposition", String, nullable=False),
        Column("revoked", Boolean, nullable=False, default=False),
        Column("revoked_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("entitlement_grant_subject_idx", grant.c.subject_id)
    Index("entitlement_grant_org_idx", grant.c.organization_id)
    return EntitlementTables(grant=grant)
