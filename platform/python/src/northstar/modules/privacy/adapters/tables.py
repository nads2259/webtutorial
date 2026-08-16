"""SQLAlchemy Core tables for the privacy data owner (schema ``northstar_privacy``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000017`` exactly and live in the
``northstar_privacy`` schema. Every table is tenant-scoped by an explicit ``organization_id`` column
— the RLS tenant column (defense-in-depth, rule 50) and the predicate every repository query
includes. The builder is parameterised on ``schema`` so portable tests can materialise the same
shape in a throwaway schema.

* ``data_field`` — the personal-data catalog: each field declares purpose + lawful basis + retention
  (EVAL-PRIV-001);
* ``consent_record`` — the versioned, APPEND-ONLY consent history keyed by
  ``(organization_id, record_id)`` with a monotonically increasing ``version`` per
  ``(subject_id, purpose)`` — a new decision inserts a new row, never updates one (EVAL-PRIV-002);
* ``rights_request`` — the access/export/erase request lifecycle records (EVAL-PRIV-003).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

PRIVACY_SCHEMA = "northstar_privacy"

PRIVACY_TENANT_TABLES: tuple[str, ...] = (
    "data_field",
    "consent_record",
    "rights_request",
)


@dataclass(frozen=True)
class PrivacyTables:
    """The privacy module tables plus the schema they live in."""

    schema: str
    data_field: Table
    consent_record: Table
    rights_request: Table


def build_privacy_tables(
    metadata: MetaData, *, schema: str | None = PRIVACY_SCHEMA
) -> PrivacyTables:
    """Define the privacy tables on ``metadata`` in ``schema`` (mirrors migration 000017)."""
    data_field = Table(
        "data_field",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("field_id", String, primary_key=True),
        Column("module_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("purpose", String, nullable=False),
        Column("lawful_basis", String, nullable=False),
        Column("data_class", String, nullable=False),
        Column("retention_days", Integer, nullable=False),
        Column("description", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("privacy_data_field_org_idx", data_field.c.organization_id)

    consent_record = Table(
        "consent_record",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("record_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("purpose", String, nullable=False),
        Column("category", String, nullable=False),
        Column("state", String, nullable=False),
        Column("lawful_basis", String, nullable=False),
        Column("version", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("privacy_consent_org_idx", consent_record.c.organization_id)
    Index(
        "privacy_consent_subject_idx",
        consent_record.c.organization_id,
        consent_record.c.subject_id,
        consent_record.c.purpose,
    )

    rights_request = Table(
        "rights_request",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("request_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("requested_by", String, nullable=False),
        Column("rights_type", String, nullable=False),
        Column("status", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("privacy_request_org_idx", rights_request.c.organization_id)
    Index(
        "privacy_request_subject_idx",
        rights_request.c.organization_id,
        rights_request.c.subject_id,
    )

    return PrivacyTables(
        schema=schema or PRIVACY_SCHEMA,
        data_field=data_field,
        consent_record=consent_record,
        rights_request=rights_request,
    )
