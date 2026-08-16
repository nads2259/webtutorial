"""000023 ai GA closers: memory correction revisions + multi-scope budget ledger + RLS.

Extends the AI data owner (``northstar_ai``) to close the two buildable GATE-AI-GA blockers without
a new schema:

* ``ai_memory`` gains nullable ``supersedes`` / ``superseded_by`` columns so a memory CORRECTION is
  an audited SUPERSEDE (a new head record that supersedes the prior revision) rather than an
  in-place mutation, preserving the revision history for a portable export and a complete erase
  (FR-AI-006, EVAL-AI-006);
* ``ai_budget`` — configured multi-scope cost budgets (per-actor / per-tenant / per-workflow),
  tenant-scoped (FR-AI-008);
* ``ai_cost_ledger`` — the recorded provider cost per AI interaction, tenant-scoped, reconciled
  against the budget ledger (FR-AI-008/009, EVAL-AI-008).

The two new tables carry a NOT NULL ``organization_id`` and get PostgreSQL Row-Level Security
ENABLED + FORCED with a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, matching
the pre-existing ``ai_memory``/``ai_trace`` tables (rule 50, defense-in-depth). Additive and
reversible; mirrors ``modules.ai.adapters.tables`` exactly. Manifest: ``000023_ai_ga_closers.json``.

Revision ID: 000023
Revises: 000022
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000023"
down_revision: str | None = "000022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_ai"
_NEW_TENANT_TABLES = ("ai_budget", "ai_cost_ledger")


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_SCHEMA}.ai_memory ADD COLUMN IF NOT EXISTS supersedes text")
    op.execute(f"ALTER TABLE {_SCHEMA}.ai_memory ADD COLUMN IF NOT EXISTS superseded_by text")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.ai_budget (
          budget_id text PRIMARY KEY,
          organization_id text NOT NULL,
          scope text NOT NULL,
          scope_id text NOT NULL,
          limit_units double precision NOT NULL,
          budget_window text NOT NULL DEFAULT 'monthly',
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ai_budget_scope_idx "
        f"ON {_SCHEMA}.ai_budget (organization_id, scope, scope_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.ai_cost_ledger (
          entry_id text PRIMARY KEY,
          organization_id text NOT NULL,
          actor_id text NOT NULL,
          workflow_id text,
          cost_units double precision NOT NULL,
          provider_cost double precision NOT NULL,
          provider text NOT NULL,
          correlation_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ai_cost_ledger_org_idx "
        f"ON {_SCHEMA}.ai_cost_ledger (organization_id, actor_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ai_cost_ledger_workflow_idx "
        f"ON {_SCHEMA}.ai_cost_ledger (organization_id, workflow_id)"
    )

    connection = op.get_bind()
    for table in _NEW_TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_NEW_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
        op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.{table}")
    op.execute(f"ALTER TABLE {_SCHEMA}.ai_memory DROP COLUMN IF EXISTS superseded_by")
    op.execute(f"ALTER TABLE {_SCHEMA}.ai_memory DROP COLUMN IF EXISTS supersedes")
