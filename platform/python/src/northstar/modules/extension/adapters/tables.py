"""SQLAlchemy Core tables for the extension data owner (schema ``northstar_extension``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000012_extension`` exactly and
live in the ``northstar_extension`` schema. Every table is tenant-scoped by an explicit
``organization_id`` column — the RLS tenant column (defense-in-depth, rule 50) and the predicate
every repository query includes. The builder is parameterised on ``schema`` so portable tests can
materialise the same shape in a throwaway schema.
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
)
from sqlalchemy.dialects.postgresql import JSONB

EXTENSION_SCHEMA = "northstar_extension"

# Every extension table is tenant-scoped and receives FORCE ROW LEVEL SECURITY (rule 50).
EXTENSION_TENANT_TABLES: tuple[str, ...] = (
    "extension_installation",
    "catalog_listing",
    "theme_application",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class ExtensionTables:
    """The extension module tables plus the schema they live in."""

    schema: str
    extension_installation: Table
    catalog_listing: Table
    theme_application: Table


def build_extension_tables(
    metadata: MetaData, *, schema: str | None = EXTENSION_SCHEMA
) -> ExtensionTables:
    """Define the extension tables on ``metadata`` in ``schema`` (mirrors migration 000012)."""
    extension_installation = Table(
        "extension_installation",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("extension_id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("publisher_id", String, nullable=False),
        Column("extension_type", String, nullable=False),
        Column("required_trust_tier", String, nullable=False),
        Column("granted_trust_tier", String, nullable=False),
        Column("permissions", _jsonb(), nullable=False),
        Column("package_digest", String, nullable=False),
        Column("uninstall_policy", String, nullable=False),
        Column("state", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("extension_installation_org_idx", extension_installation.c.organization_id)

    catalog_listing = Table(
        "catalog_listing",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("extension_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("publisher_id", String, nullable=False),
        Column("verified", Boolean, nullable=False, server_default="false"),
        Column("permissions", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("extension_catalog_listing_org_idx", catalog_listing.c.organization_id)

    theme_application = Table(
        "theme_application",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("theme_id", String, primary_key=True),
        Column("version", String, nullable=False),
        Column("presentation", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("extension_theme_application_org_idx", theme_application.c.organization_id)

    return ExtensionTables(
        schema=schema or EXTENSION_SCHEMA,
        extension_installation=extension_installation,
        catalog_listing=catalog_listing,
        theme_application=theme_application,
    )
