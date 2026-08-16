"""000026 kernel saga state (northstar_runtime.saga_state) + forced RLS.

Adds the kernel workflow/saga runtime's durable terminal-state table so a long-running,
multi-step business process uses durable workflow state + compensation rather than request-thread
orchestration (FR-KRN-004, EVAL-KRN-004):

* ``saga_state`` — the saga coordinator's OWN durable terminal state keyed by
  ``(organization_id, saga_id)`` with the terminal ``status`` (``committed`` | ``compensated``),
  the applied ``completed_steps`` and the ``compensated_steps`` run in reverse on failure.
  Persisting the terminal outcome makes re-executing a saga id idempotent across process restarts.

PostgreSQL Row-Level Security is enabled + FORCED on the new (tenant-scoped) table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role can never
read another tenant's saga state even if a predicate were ever omitted (rule 50). Expand-only and
reversible. Manifest: ``000026_kernel_saga_state.json`` (owner ``northstar.kernel``).

Revision ID: 000026
Revises: 000025
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000026"
down_revision: str | None = "000025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_SCHEMA = "northstar_runtime"
_TENANT_TABLES = ("saga_state",)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS northstar_runtime")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RUNTIME_SCHEMA}.saga_state (
          organization_id text NOT NULL,
          saga_id text NOT NULL,
          status text NOT NULL CHECK (status IN ('committed','compensated')),
          completed_steps jsonb NOT NULL,
          compensated_steps jsonb NOT NULL,
          error text,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, saga_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS saga_state_org_idx "
        f"ON {_RUNTIME_SCHEMA}.saga_state (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(
            connection, schema=_RUNTIME_SCHEMA, table=table, tenant_column="organization_id"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_RUNTIME_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_RUNTIME_SCHEMA}.saga_state")
