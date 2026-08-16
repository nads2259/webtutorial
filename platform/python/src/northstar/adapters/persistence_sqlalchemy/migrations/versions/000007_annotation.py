"""000007 annotation (northstar_annotation: annotation + annotation_moderation) + RLS.

Creates the annotation module's owned schema and tables (``annotation`` and
``annotation_moderation``) in ``northstar_annotation``, then enables PostgreSQL Row-Level Security
as defense-in-depth (FR-POL-004): every tenant-scoped table gets ``FORCE ROW LEVEL SECURITY`` and
a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC. Each annotation stores its
ORIGINAL target (``source_revision_id`` + ``selectors``) immutably and a separate ``current_remap``
provenance column (FR-ANN-003/004). Mirrors ``modules.annotation.adapters.tables``. Expand-only and
reversible (downgrade drops the policies, tables and schema it created). Manifest:
``000007_annotation.json`` (owner ``northstar.annotation``).

Revision ID: 000007
Revises: 000006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000007"
down_revision: str | None = "000006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_annotation"
_TENANT_TABLES = (
    "annotation",
    "annotation_moderation",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.annotation (
          annotation_id text PRIMARY KEY,
          organization_id text NOT NULL,
          object_id text NOT NULL,
          source_revision_id text NOT NULL,
          motivation text NOT NULL,
          body_type text NOT NULL,
          body_content jsonb NOT NULL,
          body_locale text,
          selectors jsonb NOT NULL,
          source_fingerprint text,
          visibility text NOT NULL,
          audience_ids jsonb NOT NULL,
          state text NOT NULL,
          thread_id text,
          parent_annotation_id text,
          current_remap jsonb,
          creator jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          policy_decision_id text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS annotation_org_idx ON {_SCHEMA}.annotation (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS annotation_object_idx ON {_SCHEMA}.annotation (object_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS annotation_thread_idx ON {_SCHEMA}.annotation (thread_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.annotation_moderation (
          moderation_id text PRIMARY KEY,
          annotation_id text NOT NULL,
          organization_id text NOT NULL,
          kind text NOT NULL,
          reason text,
          actor jsonb NOT NULL,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS annotation_moderation_org_idx "
        f"ON {_SCHEMA}.annotation_moderation (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS annotation_moderation_annotation_idx "
        f"ON {_SCHEMA}.annotation_moderation (annotation_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.annotation_moderation")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.annotation")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
