"""SQLAlchemy Core tables for the messaging data owner (schema ``northstar_messaging``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000013_messaging`` exactly and
live in the ``northstar_messaging`` schema. Every table is tenant-scoped by an explicit
``organization_id`` column — the RLS tenant column (defense-in-depth, rule 50) and the predicate
every repository query includes. The builder is parameterised on ``schema`` so portable tests can
materialise the same shape in a throwaway schema.

The ``delivery_receipt`` primary key ``(organization_id, campaign_id, recipient_id,
idempotency_key)`` is what makes provider submission idempotent (FR-MSG-006): a re-submission
collides on the key instead of creating a duplicate delivery. The ``template_version`` primary key
``(organization_id, template_id, version)`` enforces that a published version is immutable
(FR-MSG-002): republishing that version is a primary-key collision.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

MESSAGING_SCHEMA = "northstar_messaging"

# Every messaging table is tenant-scoped and receives FORCE ROW LEVEL SECURITY (rule 50).
MESSAGING_TENANT_TABLES: tuple[str, ...] = (
    "template_version",
    "campaign",
    "consent_record",
    "suppression_entry",
    "delivery_receipt",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class MessagingTables:
    """The messaging module tables plus the schema they live in."""

    schema: str
    template_version: Table
    campaign: Table
    consent_record: Table
    suppression_entry: Table
    delivery_receipt: Table


def build_messaging_tables(
    metadata: MetaData, *, schema: str | None = MESSAGING_SCHEMA
) -> MessagingTables:
    """Define the messaging tables on ``metadata`` in ``schema`` (mirrors migration 000013)."""
    template_version = Table(
        "template_version",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("template_id", String, primary_key=True),
        Column("version", Integer, primary_key=True),
        Column("subject", String, nullable=False),
        Column("html_body", String, nullable=False),
        Column("text_body", String, nullable=False),
        Column("required_variables", _jsonb(), nullable=False),
        Column("content_hash", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("messaging_template_version_org_idx", template_version.c.organization_id)

    campaign = Table(
        "campaign",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("campaign_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("message_class", String, nullable=False),
        Column("template_id", String, nullable=False),
        Column("template_version", Integer, nullable=False),
        Column("channel", String, nullable=False),
        Column("purpose", String, nullable=False),
        Column("segment", _jsonb(), nullable=False),
        Column("schedule", _jsonb(), nullable=False),
        Column("open_tracking", Boolean, nullable=False, server_default="false"),
        Column("click_tracking", Boolean, nullable=False, server_default="false"),
        Column("status", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("messaging_campaign_org_idx", campaign.c.organization_id)

    consent_record = Table(
        "consent_record",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("recipient_id", String, primary_key=True),
        Column("channel", String, primary_key=True),
        Column("purpose", String, primary_key=True),
        Column("consented", Boolean, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("messaging_consent_org_idx", consent_record.c.organization_id)

    suppression_entry = Table(
        "suppression_entry",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("recipient_id", String, primary_key=True),
        Column("reason", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("messaging_suppression_org_idx", suppression_entry.c.organization_id)

    delivery_receipt = Table(
        "delivery_receipt",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("campaign_id", String, primary_key=True),
        Column("recipient_id", String, primary_key=True),
        Column("idempotency_key", String, primary_key=True),
        Column("provider_message_id", String, nullable=False),
        Column("status", String, nullable=False),
        Column("send_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("messaging_delivery_org_idx", delivery_receipt.c.organization_id)
    Index("messaging_delivery_campaign_idx", delivery_receipt.c.campaign_id)

    return MessagingTables(
        schema=schema or MESSAGING_SCHEMA,
        template_version=template_version,
        campaign=campaign,
        consent_record=consent_record,
        suppression_entry=suppression_entry,
        delivery_receipt=delivery_receipt,
    )
