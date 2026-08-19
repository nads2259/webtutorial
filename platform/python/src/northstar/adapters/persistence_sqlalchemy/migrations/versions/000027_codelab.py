"""000027 codelab (northstar_codelab: tracked code-run evidence) + RLS.

Creates the codelab module's owned schema and the append-only ``code_run`` table: an IMMUTABLE,
tenant-scoped log of every learner code execution (submitted code, stdout/stderr, exit, timing,
outcome, integrity hash). PostgreSQL Row-Level Security is enabled + FORCED with a tenant-isolation
policy keyed to the ``northstar.tenant_id`` GUC (rule 50, defense-in-depth). Mirrors
``modules.codelab.adapters.tables`` exactly. Expand-only and reversible.

Revision ID: 000027
Revises: 000026
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000027"
down_revision: str | None = "000026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_codelab"
_TENANT_TABLES = ("code_run",)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.code_run (
          run_id text PRIMARY KEY,
          organization_id text NOT NULL,
          actor_id text NOT NULL,
          language text NOT NULL,
          code text NOT NULL,
          lesson_id text NULL,
          block_id text NULL,
          stdout text NOT NULL,
          stderr text NOT NULL,
          exit_code integer NOT NULL,
          duration_ms integer NOT NULL,
          timed_out boolean NOT NULL,
          truncated boolean NOT NULL,
          outcome text NOT NULL,
          record_sha256 text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS codelab_code_run_org_idx "
        f"ON {_SCHEMA}.code_run (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS codelab_code_run_actor_idx "
        f"ON {_SCHEMA}.code_run (organization_id, actor_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS codelab_code_run_created_idx "
        f"ON {_SCHEMA}.code_run (created_at)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.code_run")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
