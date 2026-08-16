"""SQLAlchemy Core tables for the retrieval data owner (schema ``northstar_retrieval``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000008_retrieval`` exactly
and live in the ``northstar_retrieval`` schema on PostgreSQL. They are DERIVED projections over
PUBLISHED knowledge (docs/06 §1/§6) — never authoritative — carrying:

* ``embedding_profile`` — the versioned provider/model/dimensions/metric/chunker registry
  (FR-RET-003);
* ``knowledge_chunk`` — one FTS-bearing chunk per published block, with its ACL attributes
  (``organization_id`` tenant column + ``visibility``/``owner_id``) and a language-aware
  ``tsvector`` column indexed with GIN (FR-RET-002);
* ``chunk_embedding`` — the pgvector ``vector`` embedding for a chunk under a profile (FR-RET-003).

Every tenant-scoped table carries an explicit ``organization_id`` — the RLS tenant column
(defense-in-depth, FR-RET-006) and the predicate every repository query includes. The builder is
parameterised on ``schema`` so portable tests can materialise the same shape in another schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from .embedding import LOCAL_EMBEDDING_DIMENSIONS

RETRIEVAL_SCHEMA = "northstar_retrieval"

# The vector dimension is profile-specific (docs/06 §6); the reference local profile fixes it. A
# production profile with different dimensions uses its own table/partition (FR-RET-004).
RETRIEVAL_VECTOR_DIMENSIONS = LOCAL_EMBEDDING_DIMENSIONS

# Tenant-scoped tables that receive FORCE ROW LEVEL SECURITY (embedding_profile is global config).
RETRIEVAL_TENANT_TABLES: tuple[str, ...] = ("knowledge_chunk", "chunk_embedding")


@dataclass(frozen=True)
class RetrievalTables:
    """The retrieval projection tables plus the schema they live in."""

    schema: str
    embedding_profile: Table
    knowledge_chunk: Table
    chunk_embedding: Table


def build_retrieval_tables(
    metadata: MetaData,
    *,
    schema: str | None = RETRIEVAL_SCHEMA,
    dimensions: int = RETRIEVAL_VECTOR_DIMENSIONS,
) -> RetrievalTables:
    """Define the retrieval tables on ``metadata`` in ``schema`` (mirrors migration 000008)."""
    embedding_profile = Table(
        "embedding_profile",
        metadata,
        Column("profile_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("provider", String, nullable=False),
        Column("model", String, nullable=False),
        Column("dimensions", Integer, nullable=False),
        Column("distance_metric", String, nullable=False),
        Column("chunker_version", String, nullable=False),
        Column("active", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    knowledge_chunk = Table(
        "knowledge_chunk",
        metadata,
        Column("chunk_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("object_id", String, nullable=False),
        Column("revision_id", String, nullable=False),
        Column("block_id", String, nullable=False),
        Column("ordinal", Integer, nullable=False),
        Column("text_content", String, nullable=False),
        Column("language", String, nullable=False),
        Column("visibility", String, nullable=False),
        Column("owner_id", String, nullable=True),
        Column("content_sha256", String, nullable=False),
        Column("tsv", TSVECTOR, nullable=True),
        Column("metadata", JSONB, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("revision_id", "block_id", "ordinal", name="knowledge_chunk_source_uq"),
        schema=schema,
    )
    Index("knowledge_chunk_org_idx", knowledge_chunk.c.organization_id)
    Index("knowledge_chunk_revision_idx", knowledge_chunk.c.revision_id)
    Index("knowledge_chunk_tsv_idx", knowledge_chunk.c.tsv, postgresql_using="gin")

    chunk_embedding = Table(
        "chunk_embedding",
        metadata,
        Column("chunk_id", String, primary_key=True),
        Column("profile_id", String, primary_key=True),
        Column("profile_version", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("vector", Vector(dimensions), nullable=False),
        Column("input_hash", String, nullable=False),
        Column("generated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("chunk_embedding_org_idx", chunk_embedding.c.organization_id)

    return RetrievalTables(
        schema=schema or RETRIEVAL_SCHEMA,
        embedding_profile=embedding_profile,
        knowledge_chunk=knowledge_chunk,
        chunk_embedding=chunk_embedding,
    )
