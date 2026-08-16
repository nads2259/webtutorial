"""SQLAlchemy Core tables for the annotation data owner (schema ``northstar_annotation``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000007_annotation`` exactly
and live in the ``northstar_annotation`` schema on PostgreSQL. Every tenant-scoped table carries an
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

ANNOTATION_SCHEMA = "northstar_annotation"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class AnnotationTables:
    """Core tables backing annotations and their moderation evidence."""

    annotation: Table
    moderation: Table


def build_annotation_tables(
    metadata: MetaData, *, schema: str | None = ANNOTATION_SCHEMA
) -> AnnotationTables:
    """Define the annotation tables on ``metadata`` in ``schema`` (mirrors migration 000007)."""
    annotation = Table(
        "annotation",
        metadata,
        Column("annotation_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("object_id", String, nullable=False),
        Column("source_revision_id", String, nullable=False),
        Column("motivation", String, nullable=False),
        Column("body_type", String, nullable=False),
        Column("body_content", _jsonb(), nullable=False),
        Column("body_locale", String, nullable=True),
        Column("selectors", _jsonb(), nullable=False),
        Column("source_fingerprint", String, nullable=True),
        Column("visibility", String, nullable=False),
        Column("audience_ids", _jsonb(), nullable=False),
        Column("state", String, nullable=False),
        Column("thread_id", String, nullable=True),
        Column("parent_annotation_id", String, nullable=True),
        Column("current_remap", _jsonb(), nullable=True),
        Column("creator", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("policy_decision_id", String, nullable=True),
        schema=schema,
    )
    Index("annotation_org_idx", annotation.c.organization_id)
    Index("annotation_object_idx", annotation.c.object_id)
    Index("annotation_thread_idx", annotation.c.thread_id)

    moderation = Table(
        "annotation_moderation",
        metadata,
        Column("moderation_id", String, primary_key=True),
        Column("annotation_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("reason", String, nullable=True),
        Column("actor", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("annotation_moderation_org_idx", moderation.c.organization_id)
    Index("annotation_moderation_annotation_idx", moderation.c.annotation_id)

    return AnnotationTables(annotation=annotation, moderation=moderation)
