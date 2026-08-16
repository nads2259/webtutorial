"""000008 retrieval (northstar_retrieval: embedding_profile/knowledge_chunk/chunk_embedding) + RLS.

Creates the retrieval module's owned schema and DERIVED projection tables over PUBLISHED knowledge
content (docs/06 §1/§6): the versioned ``embedding_profile`` registry (FR-RET-003), a
``knowledge_chunk`` FTS projection with a language-aware ``tsvector`` (GIN indexed) carrying each
chunk's ACL attributes (``organization_id``/``visibility``/``owner_id``), and a ``chunk_embedding``
table holding pgvector ``vector`` embeddings for EXACT search (FR-RET-002/005). The pgvector
extension is ensured first.

PostgreSQL Row-Level Security is then enabled as defense-in-depth (FR-RET-006, rule 50): the
tenant-scoped ``knowledge_chunk`` and ``chunk_embedding`` tables get ``FORCE ROW LEVEL SECURITY``
and a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role
cannot read another tenant's passages even if an application predicate were ever omitted. Only an
EXACT semantic index is built here — no HNSW/IVFFlat without a recorded benchmark (FR-RET-005).
Mirrors ``modules.retrieval.adapters.tables`` exactly. Expand-only and reversible (downgrade drops
the policies, tables and schema it created). Manifest: ``000008_retrieval.json`` (owner
``northstar.retrieval``).

Revision ID: 000008
Revises: 000007
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000008"
down_revision: str | None = "000007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_retrieval"
# The reference local embedding profile fixes the vector dimension (docs/06 §6). A profile with a
# different dimensionality uses its own table/partition; not enabled here.
_DIMENSIONS = 256
_TENANT_TABLES = (
    "knowledge_chunk",
    "chunk_embedding",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.embedding_profile (
          profile_id text NOT NULL,
          version text NOT NULL,
          provider text NOT NULL,
          model text NOT NULL,
          dimensions integer NOT NULL CHECK (dimensions > 0),
          distance_metric text NOT NULL,
          chunker_version text NOT NULL,
          active boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (profile_id, version)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.knowledge_chunk (
          chunk_id text PRIMARY KEY,
          organization_id text NOT NULL,
          object_id text NOT NULL,
          revision_id text NOT NULL,
          block_id text NOT NULL,
          ordinal integer NOT NULL,
          text_content text NOT NULL,
          language text NOT NULL,
          visibility text NOT NULL,
          owner_id text,
          content_sha256 text NOT NULL,
          tsv tsvector,
          metadata jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT knowledge_chunk_source_uq UNIQUE (revision_id, block_id, ordinal)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS knowledge_chunk_org_idx "
        f"ON {_SCHEMA}.knowledge_chunk (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS knowledge_chunk_revision_idx "
        f"ON {_SCHEMA}.knowledge_chunk (revision_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS knowledge_chunk_tsv_idx "
        f"ON {_SCHEMA}.knowledge_chunk USING gin (tsv)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.chunk_embedding (
          chunk_id text NOT NULL,
          profile_id text NOT NULL,
          profile_version text NOT NULL,
          organization_id text NOT NULL,
          vector vector({_DIMENSIONS}) NOT NULL,
          input_hash text NOT NULL,
          generated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (chunk_id, profile_id, profile_version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS chunk_embedding_org_idx "
        f"ON {_SCHEMA}.chunk_embedding (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.chunk_embedding")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.knowledge_chunk")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.embedding_profile")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
