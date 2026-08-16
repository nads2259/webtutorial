"""SQLAlchemy Core tables for the analytics data owner (schema ``northstar_analytics``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000014_analytics`` exactly and
live in the ``northstar_analytics`` schema. Every table is tenant-scoped by an explicit
``organization_id`` column — the RLS tenant column (defense-in-depth, rule 50) and the predicate
every repository query includes. The builder is parameterised on ``schema`` so portable tests can
materialise the same shape in a throwaway schema.

The ``event_definition`` primary key ``(organization_id, event_name, version)`` makes a registered
event type immutable (FR-ANL-003): re-registering a version is a primary-key collision. First-party
``event`` rows are the AUTHORITATIVE analytics source (FR-ANL-001/002). GA4 figures are deliberately
NOT persisted here — they are non-authoritative imports retained minimally by the caller (docs/17
§9).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

ANALYTICS_SCHEMA = "northstar_analytics"

# Every analytics table is tenant-scoped and receives FORCE ROW LEVEL SECURITY (rule 50).
ANALYTICS_TENANT_TABLES: tuple[str, ...] = (
    "event_definition",
    "event",
    "identity_stitch",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class AnalyticsTables:
    """The analytics module tables plus the schema they live in."""

    schema: str
    event_definition: Table
    event: Table
    identity_stitch: Table


def build_analytics_tables(
    metadata: MetaData, *, schema: str | None = ANALYTICS_SCHEMA
) -> AnalyticsTables:
    """Define the analytics tables on ``metadata`` in ``schema`` (mirrors migration 000014)."""
    event_definition = Table(
        "event_definition",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("event_name", String, primary_key=True),
        Column("version", Integer, primary_key=True),
        Column("owner", String, nullable=False),
        Column("purpose", String, nullable=False),
        Column("consent_category", String, nullable=False),
        Column("retention_days", Integer, nullable=False),
        Column("destinations", _jsonb(), nullable=False),
        Column("properties", _jsonb(), nullable=False),
        Column("trigger", String, nullable=True),
        Column("sampling", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("analytics_event_definition_org_idx", event_definition.c.organization_id)

    event = Table(
        "event",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("event_id", String, primary_key=True),
        Column("event_name", String, nullable=False),
        Column("event_version", Integer, nullable=False),
        Column("actor_type", String, nullable=False),
        Column("actor_id", String, nullable=False),
        Column("anonymous_id", String, nullable=True),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("properties", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("analytics_event_org_idx", event.c.organization_id)
    Index("analytics_event_name_idx", event.c.organization_id, event.c.event_name)

    identity_stitch = Table(
        "identity_stitch",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("anonymous_id", String, primary_key=True),
        Column("user_id", String, primary_key=True),
        Column("consent_category", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("analytics_identity_stitch_org_idx", identity_stitch.c.organization_id)

    return AnalyticsTables(
        schema=schema or ANALYTICS_SCHEMA,
        event_definition=event_definition,
        event=event,
        identity_stitch=identity_stitch,
    )
