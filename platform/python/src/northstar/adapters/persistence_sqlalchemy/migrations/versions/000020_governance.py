"""000020 governance (northstar_governance: governance_decision, governance_exception) + forced RLS.

Creates the governance module's owned schema and tables in ``northstar_governance`` (FR-GOV-001/002,
EVAL-GOV-001/002). ``governance_decision`` holds immutable, traceable decision records (decider +
rationale + status + links to affected controls/requirements/gates; a ``supersedes`` link to the
prior record for corrections — the prior is never mutated, LAW-07). ``governance_exception`` holds
scoped, approved, time-bounded control exceptions (approver + granted_by + explicit ``expiry`` +
status; ``revoked_by``/``revoked_at`` for revocation) that auto-expire under the evaluation clock.
PostgreSQL Row-Level Security is enabled + FORCED on both tenant-scoped tables with a
tenant-isolation policy keyed to the tenant GUC (``northstar.tenant_id``), so a non-superuser role
cannot read another tenant's rows even if a predicate were omitted (rule 50). Mirrors
``modules.governance.adapters.tables`` exactly. Expand-only and reversible (downgrade drops the
policies, tables and schema it created). Manifest: ``000020_governance.json``.

Revision ID: 000020
Revises: 000019
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000020"
down_revision: str | None = "000019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_governance"
_TENANT_TABLES = ("governance_decision", "governance_exception")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.governance_decision (
          decision_id text PRIMARY KEY,
          organization_id text NOT NULL,
          title text NOT NULL,
          status text NOT NULL,
          rationale text NOT NULL,
          decider jsonb NOT NULL,
          links jsonb NOT NULL,
          supersedes text,
          recorded_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS governance_decision_org_idx "
        f"ON {_SCHEMA}.governance_decision (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS governance_decision_supersedes_idx "
        f"ON {_SCHEMA}.governance_decision (organization_id, supersedes)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.governance_exception (
          exception_id text PRIMARY KEY,
          organization_id text NOT NULL,
          control text NOT NULL,
          subject text NOT NULL,
          approver jsonb NOT NULL,
          granted_by jsonb NOT NULL,
          rationale text NOT NULL,
          status text NOT NULL,
          expiry timestamptz NOT NULL,
          granted_at timestamptz NOT NULL,
          revoked_by jsonb,
          revoked_at timestamptz
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS governance_exception_org_idx "
        f"ON {_SCHEMA}.governance_exception (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS governance_exception_control_idx "
        f"ON {_SCHEMA}.governance_exception (organization_id, control)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.governance_exception")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.governance_decision")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
