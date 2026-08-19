"""000029 assistant (northstar_assistant: durable admin model selection) + RLS.

Creates the assistant module's owned schema and the ``assistant_setting`` table: one row per tenant
persisting the admin-selected active chat model, so the choice survives restarts. PostgreSQL Row-Level
Security is enabled + FORCED with a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC
(rule 50, defense-in-depth). Mirrors ``modules.assistant.adapters.tables`` exactly. Reversible.

Revision ID: 000029
Revises: 000028
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000029"
down_revision: str | None = "000028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_assistant"
_TENANT_TABLES = ("assistant_setting",)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.assistant_setting (
          organization_id text PRIMARY KEY,
          active_model text NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.assistant_setting")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
