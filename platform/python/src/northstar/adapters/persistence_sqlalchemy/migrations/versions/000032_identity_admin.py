"""000032 identity admin flag (password_credential.is_admin).

Adds a boolean ``is_admin`` to the local password credential so backend/management accounts are a
distinct, seeded class separate from self-registered frontend learners. Admin accounts authenticate on
the separate management login surface; the flag gates the admin console and admin-only endpoints.

Revision ID: 000032
Revises: 000031
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000032"
down_revision: str | None = "000031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_identity"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {_SCHEMA}.password_credential "
        f"ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_SCHEMA}.password_credential DROP COLUMN IF EXISTS is_admin")
