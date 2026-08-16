"""SQLAlchemy Core tables for the enterprise data owner (schema ``northstar_enterprise``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000021_enterprise`` exactly
and live in the ``northstar_enterprise`` schema on PostgreSQL. Every tenant-scoped table carries an
explicit ``organization_id`` — the RLS tenant column (defense-in-depth, FR-POL-004) and the
predicate every repository query includes. The builder is parameterised on ``schema`` so portable
tests can materialise the same shape in another schema.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

ENTERPRISE_SCHEMA = "northstar_enterprise"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class EnterpriseTables:
    """Core tables backing federation mappings + SCIM provisioning records."""

    federation_mapping: Table
    provisioning_record: Table


def build_enterprise_tables(
    metadata: MetaData, *, schema: str | None = ENTERPRISE_SCHEMA
) -> EnterpriseTables:
    """Define the enterprise tables on ``metadata`` in ``schema`` (mirrors migration 000021)."""
    federation_mapping = Table(
        "enterprise_federation_mapping",
        metadata,
        Column("mapping_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("issuer", String, nullable=False),
        Column("external_subject", String, nullable=False),
        Column("subject_id", String, nullable=False),
        Column("user_id", String, nullable=False),
        Column("linked_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint(
            "organization_id",
            "issuer",
            "external_subject",
            name="enterprise_federation_mapping_identity_uq",
        ),
        schema=schema,
    )
    Index("enterprise_federation_mapping_org_idx", federation_mapping.c.organization_id)

    provisioning_record = Table(
        "enterprise_provisioning_record",
        metadata,
        Column("record_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("resource_type", String, nullable=False),
        Column("external_id", String, nullable=False),
        Column("active", Boolean, nullable=False),
        Column("subject_id", String, nullable=True),
        Column("display_name", String, nullable=True),
        Column("email", String, nullable=True),
        Column("members", _jsonb(), nullable=False),
        Column("provisioned_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("deactivated_at", DateTime(timezone=True), nullable=True),
        UniqueConstraint(
            "organization_id",
            "external_id",
            name="enterprise_provisioning_record_external_uq",
        ),
        schema=schema,
    )
    Index("enterprise_provisioning_record_org_idx", provisioning_record.c.organization_id)

    return EnterpriseTables(
        federation_mapping=federation_mapping,
        provisioning_record=provisioning_record,
    )
