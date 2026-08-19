"""000031 messaging mailbox (durable transactional-email outbox) + RLS.

Adds ``northstar_messaging.email_message``: the durable record of every transactional email (rendered
subject/body, delivery status, provider id) that powers the admin Outbox and the dev "mailbox". Default
transactional templates (account-confirmation, password-reset) are provided in code and become
DB-backed once an admin publishes an edited version, so no seed rows are required here. Tenant-scoped
by ``organization_id`` with PostgreSQL Row-Level Security enabled + FORCED (rule 50).

Revision ID: 000031
Revises: 000030
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000031"
down_revision: str | None = "000030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_messaging"
_TENANT_TABLES = ("email_message",)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.email_message (
          message_id text PRIMARY KEY,
          organization_id text NOT NULL,
          to_email text NOT NULL,
          template_id text NULL,
          subject text NOT NULL,
          html_body text NOT NULL,
          text_body text NOT NULL,
          status text NOT NULL,
          provider_message_id text NULL,
          error text NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_email_message_org_idx "
        f"ON {_SCHEMA}.email_message (organization_id, created_at)"
    )
    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.email_message")
