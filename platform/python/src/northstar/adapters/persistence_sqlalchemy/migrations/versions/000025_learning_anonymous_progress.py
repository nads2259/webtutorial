"""000025 learning anonymous progress (northstar_learning.anonymous_progress) + forced RLS.

Adds the learning module's anonymous, device/session-scoped progress table so a visitor's progress
(captured under an anonymous id before sign-in) can be MERGED into their authenticated account on
sign-in — furthest position wins, no loss, no duplicate, idempotent and tenant-scoped (UX-010,
FR-LRN-002):

* ``anonymous_progress`` — the module's OWN anonymous progress state keyed by
  ``(organization_id, anonymous_id, course_id)`` with a stable ``resume`` position, mirroring the
  authenticated ``progress`` shape; ``claimed_by`` records the authenticated subject that has merged
  the record so it can never be re-claimed by a different subject (cross-owner refusal, LAW-08).

PostgreSQL Row-Level Security is enabled + FORCED on the new (tenant-scoped) table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role can never
read another tenant's anonymous progress even if a predicate were ever omitted (rule 50). Mirrors
``learning.adapters.tables`` exactly. Expand-only and reversible. Manifest:
``000025_learning_anonymous_progress.json`` (owner ``northstar.learning``).

Revision ID: 000025
Revises: 000024
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000025"
down_revision: str | None = "000024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEARNING_SCHEMA = "northstar_learning"
_TENANT_TABLES = ("anonymous_progress",)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.anonymous_progress (
          organization_id text NOT NULL,
          anonymous_id text NOT NULL,
          course_id text NOT NULL,
          resume jsonb NOT NULL,
          modality text NOT NULL,
          completed_sections jsonb NOT NULL,
          claimed_by text,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, anonymous_id, course_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_anon_progress_org_idx "
        f"ON {_LEARNING_SCHEMA}.anonymous_progress (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_anon_progress_device_idx "
        f"ON {_LEARNING_SCHEMA}.anonymous_progress (organization_id, anonymous_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(
            connection, schema=_LEARNING_SCHEMA, table=table, tenant_column="organization_id"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_LEARNING_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_LEARNING_SCHEMA}.anonymous_progress")
