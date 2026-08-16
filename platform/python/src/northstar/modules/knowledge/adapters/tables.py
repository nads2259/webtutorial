"""SQLAlchemy Core tables for the knowledge data owner (schema ``northstar_knowledge``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000006_knowledge`` exactly
and live in the ``northstar_knowledge`` schema on PostgreSQL. Every tenant-scoped table carries an
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
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

KNOWLEDGE_SCHEMA = "northstar_knowledge"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class KnowledgeTables:
    """Core tables backing knowledge objects, drafts, revisions, blocks, publications, taxonomy."""

    knowledge_object: Table
    draft: Table
    revision: Table
    block: Table
    publication: Table
    taxonomy_assignment: Table


def build_knowledge_tables(
    metadata: MetaData, *, schema: str | None = KNOWLEDGE_SCHEMA
) -> KnowledgeTables:
    """Define the knowledge tables on ``metadata`` in ``schema`` (mirrors migration 000006)."""
    knowledge_object = Table(
        "knowledge_object",
        metadata,
        Column("object_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_type", String, nullable=False),
        Column("canonical_locale", String, nullable=False),
        Column("lifecycle", String, nullable=False),
        Column("latest_revision_id", String, nullable=True),
        schema=schema,
    )
    Index("knowledge_object_org_idx", knowledge_object.c.organization_id)

    draft = Table(
        "draft",
        metadata,
        Column("draft_id", String, primary_key=True),
        Column("object_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("base_revision_id", String, nullable=True),
        Column("content_tree", _jsonb(), nullable=False),
        Column("version", Integer, nullable=False),
        schema=schema,
    )
    Index("draft_object_idx", draft.c.object_id, unique=True)
    Index("draft_org_idx", draft.c.organization_id)

    revision = Table(
        "revision",
        metadata,
        Column("revision_id", String, primary_key=True),
        Column("object_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("parent_revision_id", String, nullable=True),
        Column("document_type", String, nullable=False),
        Column("locale", String, nullable=False),
        Column("title", String, nullable=False),
        Column("summary", String, nullable=True),
        Column("content_tree", _jsonb(), nullable=False),
        Column("content_hash", String, nullable=False),
        Column("schema_version", String, nullable=False),
        Column("created_by", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("revision_object_idx", revision.c.object_id)
    Index("revision_org_idx", revision.c.organization_id)

    block = Table(
        "block",
        metadata,
        Column("revision_id", String, primary_key=True),
        Column("block_id", String, primary_key=True),
        Column("object_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("block_type", String, nullable=False),
        Column("ordinal", Integer, nullable=False),
        Column("path", String, nullable=False),
        schema=schema,
    )
    Index("block_org_idx", block.c.organization_id)
    Index("block_revision_idx", block.c.revision_id)

    publication = Table(
        "publication",
        metadata,
        Column("publication_id", String, primary_key=True),
        Column("object_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("revision_id", String, nullable=False),
        Column("channel", String, nullable=False),
        Column("locale", String, nullable=False),
        Column("visibility", String, nullable=False),
        Column("published_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("publication_org_idx", publication.c.organization_id)
    Index("publication_object_idx", publication.c.object_id)

    taxonomy_assignment = Table(
        "taxonomy_assignment",
        metadata,
        Column("assignment_id", String, primary_key=True),
        Column("object_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("scheme", String, nullable=False),
        Column("term", String, nullable=False),
        schema=schema,
    )
    Index("taxonomy_org_idx", taxonomy_assignment.c.organization_id)
    Index("taxonomy_object_idx", taxonomy_assignment.c.object_id)

    return KnowledgeTables(
        knowledge_object=knowledge_object,
        draft=draft,
        revision=revision,
        block=block,
        publication=publication,
        taxonomy_assignment=taxonomy_assignment,
    )
