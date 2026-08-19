"""000030 identity local auth (password_credential, verification_token, account_event) + RLS.

Adds local (email + password) authentication to the existing ``northstar_identity`` schema: a salted
scrypt password credential per user (email unique per tenant), single-use expiring verification tokens
(email confirmation + password reset; only the token SHA-256 is stored), and a durable append-only
``account_event`` log surfaced under Activity. All three are tenant-scoped by ``organization_id`` with
PostgreSQL Row-Level Security enabled + FORCED (rule 50). Mirrors ``modules.identity.adapters.tables``.

Revision ID: 000030
Revises: 000029
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000030"
down_revision: str | None = "000029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_identity"
_TENANT_TABLES = ("password_credential", "verification_token", "account_event")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.password_credential (
          user_id text PRIMARY KEY,
          subject_id text NOT NULL,
          organization_id text NOT NULL,
          email text NOT NULL,
          password_hash text NOT NULL,
          email_verified boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_password_credential_email "
        f"ON {_SCHEMA}.password_credential (organization_id, email)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS password_credential_subject_idx "
        f"ON {_SCHEMA}.password_credential (subject_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.verification_token (
          token_id text PRIMARY KEY,
          organization_id text NOT NULL,
          token_sha256 text NOT NULL,
          purpose text NOT NULL,
          subject_id text NOT NULL,
          email text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz NULL
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_verification_token_hash "
        f"ON {_SCHEMA}.verification_token (token_sha256)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.account_event (
          event_id text PRIMARY KEY,
          organization_id text NOT NULL,
          subject_id text NOT NULL,
          event_type text NOT NULL,
          detail text NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS account_event_subject_idx "
        f"ON {_SCHEMA}.account_event (organization_id, subject_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS account_event_tenant_idx "
        f"ON {_SCHEMA}.account_event (organization_id, created_at)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.account_event")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.verification_token")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.password_credential")
