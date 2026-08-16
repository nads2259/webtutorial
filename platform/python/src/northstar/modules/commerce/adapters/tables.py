"""SQLAlchemy Core tables for the commerce data owner (schema ``northstar_commerce``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000015`` exactly and live in the
``northstar_commerce`` schema. Every table is tenant-scoped by an explicit ``organization_id``
column — the RLS tenant column (defense-in-depth, rule 50) and the predicate every repository query
includes. The builder is parameterised on ``schema`` so portable tests can materialise the same
shape in a throwaway schema.

The ``payment_event`` table's ``(organization_id, event_id)`` primary key makes a processed provider
event idempotent (FR-COM-003): re-recording the same event id is a primary-key collision, so a
replayed callback has a single effect. Commerce OWNS the entitlement grants it issues for purchases
in ``entitlement_grant`` (LAW-13); the decision logic is REUSED from the entitlement engine, not
re-implemented.
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

COMMERCE_SCHEMA = "northstar_commerce"

COMMERCE_TENANT_TABLES: tuple[str, ...] = (
    "product",
    "offer",
    "purchase",
    "payment_event",
    "refund",
    "entitlement_grant",
    "ad_placement",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class CommerceTables:
    """The commerce module tables plus the schema they live in."""

    schema: str
    product: Table
    offer: Table
    purchase: Table
    payment_event: Table
    refund: Table
    entitlement_grant: Table
    ad_placement: Table


def build_commerce_tables(
    metadata: MetaData, *, schema: str | None = COMMERCE_SCHEMA
) -> CommerceTables:
    """Define the commerce tables on ``metadata`` in ``schema`` (mirrors migration 000015)."""
    product = Table(
        "product",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("product_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("commerce_product_org_idx", product.c.organization_id)

    offer = Table(
        "offer",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("offer_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("product_id", String, nullable=False),
        Column("status", String, nullable=False),
        Column("price", _jsonb(), nullable=False),
        Column("grants", _jsonb(), nullable=False),
        Column("eligibility", _jsonb(), nullable=False),
        Column("terms_version", String, nullable=False),
        Column("effective_from", DateTime(timezone=True), nullable=True),
        Column("effective_until", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("commerce_offer_org_idx", offer.c.organization_id)

    purchase = Table(
        "purchase",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("purchase_id", String, primary_key=True),
        Column("offer_id", String, nullable=False),
        Column("offer_version", String, nullable=False),
        Column("product_id", String, nullable=False),
        Column("subject_id", String, nullable=False),
        Column("status", String, nullable=False),
        Column("grant_ids", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("commerce_purchase_org_idx", purchase.c.organization_id)
    Index("commerce_purchase_subject_idx", purchase.c.organization_id, purchase.c.subject_id)

    payment_event = Table(
        "payment_event",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("event_id", String, primary_key=True),
        Column("event_type", String, nullable=False),
        Column("purchase_id", String, nullable=False),
        Column("processed_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("commerce_payment_event_org_idx", payment_event.c.organization_id)

    refund = Table(
        "refund",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("refund_id", String, primary_key=True),
        Column("purchase_id", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("commerce_refund_org_idx", refund.c.organization_id)

    entitlement_grant = Table(
        "entitlement_grant",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("grant_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("capability", String, nullable=False),
        Column("origin", String, nullable=False),
        Column("starts_at", DateTime(timezone=True), nullable=False),
        Column("ends_at", DateTime(timezone=True), nullable=True),
        Column("quota_limit", Integer, nullable=True),
        Column("quota_used", Integer, nullable=False),
        Column("quota_disposition", String, nullable=False),
        Column("revoked", Boolean, nullable=False),
        Column("revoked_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("commerce_entitlement_grant_org_idx", entitlement_grant.c.organization_id)
    Index(
        "commerce_entitlement_grant_subject_idx",
        entitlement_grant.c.organization_id,
        entitlement_grant.c.subject_id,
    )

    ad_placement = Table(
        "ad_placement",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("placement_id", String, primary_key=True),
        Column("kind", String, nullable=False),
        Column("disclosure_label", String, nullable=False),
        Column("disclosed", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("commerce_ad_placement_org_idx", ad_placement.c.organization_id)

    return CommerceTables(
        schema=schema or COMMERCE_SCHEMA,
        product=product,
        offer=offer,
        purchase=purchase,
        payment_event=payment_event,
        refund=refund,
        entitlement_grant=entitlement_grant,
        ad_placement=ad_placement,
    )
