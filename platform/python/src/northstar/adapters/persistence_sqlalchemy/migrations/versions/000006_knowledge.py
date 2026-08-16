"""000006 knowledge (northstar_knowledge: object/draft/revision/block/publication/taxonomy) + RLS.

Creates the knowledge module's owned schema and tables (``knowledge_object``, ``draft``,
``revision``, ``block``, ``publication``, ``taxonomy_assignment``) in ``northstar_knowledge``, then
enables PostgreSQL Row-Level Security as defense-in-depth (FR-POL-004): every tenant-scoped table
gets ``FORCE ROW LEVEL SECURITY`` and a tenant-isolation policy keyed to the ``northstar.tenant_id``
GUC. Content trees are stored as ``jsonb`` (the canonical typed block tree); published revisions
are immutable (enforced by the application/repository, LAW-07). Mirrors
``modules.knowledge.adapters.tables`` exactly. Expand-only and reversible (downgrade drops the
policies, tables and schema it created). Manifest: ``000006_knowledge.json`` (owner
``northstar.knowledge``).

Revision ID: 000006
Revises: 000005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000006"
down_revision: str | None = "000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_knowledge"
_TENANT_TABLES = (
    "knowledge_object",
    "draft",
    "revision",
    "block",
    "publication",
    "taxonomy_assignment",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.knowledge_object (
          object_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_type text NOT NULL,
          canonical_locale text NOT NULL,
          lifecycle text NOT NULL,
          latest_revision_id text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS knowledge_object_org_idx "
        f"ON {_SCHEMA}.knowledge_object (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.draft (
          draft_id text PRIMARY KEY,
          object_id text NOT NULL,
          organization_id text NOT NULL,
          base_revision_id text,
          content_tree jsonb NOT NULL,
          version integer NOT NULL
        )
        """
    )
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS draft_object_idx ON {_SCHEMA}.draft (object_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS draft_org_idx ON {_SCHEMA}.draft (organization_id)")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.revision (
          revision_id text PRIMARY KEY,
          object_id text NOT NULL,
          organization_id text NOT NULL,
          parent_revision_id text,
          document_type text NOT NULL,
          locale text NOT NULL,
          title text NOT NULL,
          summary text,
          content_tree jsonb NOT NULL,
          content_hash text NOT NULL,
          schema_version text NOT NULL,
          created_by jsonb NOT NULL,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS revision_object_idx ON {_SCHEMA}.revision (object_id)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS revision_org_idx ON {_SCHEMA}.revision (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.block (
          revision_id text NOT NULL,
          block_id text NOT NULL,
          object_id text NOT NULL,
          organization_id text NOT NULL,
          block_type text NOT NULL,
          ordinal integer NOT NULL,
          path text NOT NULL,
          PRIMARY KEY (revision_id, block_id)
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS block_org_idx ON {_SCHEMA}.block (organization_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS block_revision_idx ON {_SCHEMA}.block (revision_id)")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.publication (
          publication_id text PRIMARY KEY,
          object_id text NOT NULL,
          organization_id text NOT NULL,
          revision_id text NOT NULL,
          channel text NOT NULL,
          locale text NOT NULL,
          visibility text NOT NULL,
          published_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS publication_org_idx ON {_SCHEMA}.publication (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS publication_object_idx ON {_SCHEMA}.publication (object_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.taxonomy_assignment (
          assignment_id text PRIMARY KEY,
          object_id text NOT NULL,
          organization_id text NOT NULL,
          scheme text NOT NULL,
          term text NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS taxonomy_org_idx "
        f"ON {_SCHEMA}.taxonomy_assignment (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS taxonomy_object_idx "
        f"ON {_SCHEMA}.taxonomy_assignment (object_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.taxonomy_assignment")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.publication")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.block")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.revision")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.draft")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.knowledge_object")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
