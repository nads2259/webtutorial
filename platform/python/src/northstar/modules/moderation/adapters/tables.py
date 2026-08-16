"""SQLAlchemy Core tables for the moderation data owner (schema ``northstar_moderation``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000019_moderation`` exactly
and live in the ``northstar_moderation`` schema on PostgreSQL. Every tenant-scoped table carries an
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

MODERATION_SCHEMA = "northstar_moderation"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class ModerationTables:
    """Core tables backing moderation cases and their append-only lifecycle event trail."""

    case: Table
    event: Table


def build_moderation_tables(
    metadata: MetaData, *, schema: str | None = MODERATION_SCHEMA
) -> ModerationTables:
    """Define the moderation tables on ``metadata`` in ``schema`` (mirrors migration 000019)."""
    case = Table(
        "moderation_case",
        metadata,
        Column("case_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("content_type", String, nullable=False),
        Column("content_id", String, nullable=False),
        Column("author_id", String, nullable=False),
        Column("state", String, nullable=False),
        Column("reports", _jsonb(), nullable=False),
        Column("assignee_id", String, nullable=True),
        Column("decision", _jsonb(), nullable=True),
        Column("enforcement", _jsonb(), nullable=True),
        Column("appeal", _jsonb(), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("moderation_case_org_idx", case.c.organization_id)
    Index(
        "moderation_case_target_idx",
        case.c.organization_id,
        case.c.content_type,
        case.c.content_id,
    )
    Index("moderation_case_state_idx", case.c.organization_id, case.c.state)

    event = Table(
        "moderation_event",
        metadata,
        Column("event_id", String, primary_key=True),
        Column("case_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("action", String, nullable=False),
        Column("from_state", String, nullable=True),
        Column("to_state", String, nullable=False),
        Column("actor", _jsonb(), nullable=False),
        Column("rationale", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("moderation_event_org_idx", event.c.organization_id)
    Index("moderation_event_case_idx", event.c.organization_id, event.c.case_id)

    return ModerationTables(case=case, event=event)
