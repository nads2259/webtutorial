"""000003 identity (northstar_identity).

Creates the identity module's data owner: the ``northstar_identity`` schema with ``subject``,
``user_account``, ``external_identity``, ``session`` and ``credential`` tables (docs/07 §2-4).
Mirrors ``northstar.modules.identity.adapters.tables.build_identity_tables`` exactly. Security
invariants baked into the shape: the ``session`` table stores only ``token_sha256`` (never the raw
session token, rule 50); external identities are keyed by ``(issuer, subject)`` — email is not an
identity key. Expand-only and reversible (downgrade drops the schema and tables it created).
Manifest: ``000003_identity.json`` (owner ``northstar.identity``).

Revision ID: 000003
Revises: 000002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000003"
down_revision: str | None = "000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS northstar_identity")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_identity.subject (
          subject_id text PRIMARY KEY,
          subject_type text NOT NULL,
          created_at timestamptz NOT NULL,
          tenant_scope text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_identity.user_account (
          user_id text PRIMARY KEY,
          subject_id text NOT NULL REFERENCES northstar_identity.subject(subject_id),
          primary_email text,
          display_name text,
          created_at timestamptz NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_identity.external_identity (
          issuer text NOT NULL,
          subject text NOT NULL,
          user_id text NOT NULL REFERENCES northstar_identity.user_account(user_id),
          linked_at timestamptz NOT NULL,
          PRIMARY KEY (issuer, subject)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_identity.session (
          session_id text PRIMARY KEY,
          subject_id text NOT NULL,
          token_sha256 text NOT NULL,
          created_at timestamptz NOT NULL,
          idle_expires_at timestamptz NOT NULL,
          absolute_expires_at timestamptz NOT NULL,
          assurance text NOT NULL,
          tenant_scope text,
          revoked_at timestamptz,
          rotated_from text
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_token_sha256 "
        "ON northstar_identity.session (token_sha256)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS session_subject_idx ON northstar_identity.session (subject_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_identity.credential (
          credential_id text PRIMARY KEY,
          subject_id text NOT NULL,
          factor_type text NOT NULL,
          material jsonb NOT NULL,
          label text,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS credential_subject_idx "
        "ON northstar_identity.credential (subject_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS northstar_identity.credential")
    op.execute("DROP TABLE IF EXISTS northstar_identity.session")
    op.execute("DROP TABLE IF EXISTS northstar_identity.external_identity")
    op.execute("DROP TABLE IF EXISTS northstar_identity.user_account")
    op.execute("DROP TABLE IF EXISTS northstar_identity.subject")
    op.execute("DROP SCHEMA IF EXISTS northstar_identity CASCADE")
