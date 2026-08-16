"""000005 MFA credentials (northstar_identity.totp_credential + webauthn_credential).

Adds the identity module's real second-factor storage (FR-IDN-003): a ``totp_credential`` table
holding the RFC 6238 shared secret plus the monotonic ``last_used_step`` that enforces TOTP replay
protection, and a ``webauthn_credential`` table holding each passkey's COSE public key and the
signature ``sign_count`` whose regression the verifier rejects (a cloned authenticator, WebAuthn
§6.1.1). Mirrors the ``totp_credential`` / ``webauthn_credential`` tables in
``northstar.modules.identity.adapters.tables.build_identity_tables`` exactly. Expand-only and
reversible (downgrade drops only the two tables it created, leaving the rest of
``northstar_identity`` intact). Manifest: ``000005_mfa.json`` (owner ``northstar.identity``).

Revision ID: 000005
Revises: 000004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000005"
down_revision: str | None = "000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_identity"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.totp_credential (
          credential_id text PRIMARY KEY,
          subject_id text NOT NULL,
          secret text NOT NULL,
          digits integer NOT NULL,
          period integer NOT NULL,
          algorithm text NOT NULL,
          created_at timestamptz NOT NULL,
          confirmed_at timestamptz,
          last_used_step bigint,
          label text
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_totp_subject "
        f"ON {_SCHEMA}.totp_credential (subject_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.webauthn_credential (
          credential_id text PRIMARY KEY,
          subject_id text NOT NULL,
          public_key bytea NOT NULL,
          sign_count bigint NOT NULL,
          rp_id text NOT NULL,
          origin text NOT NULL,
          aaguid text,
          transports jsonb,
          label text,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS webauthn_subject_idx "
        f"ON {_SCHEMA}.webauthn_credential (subject_id)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.webauthn_credential")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.totp_credential")
