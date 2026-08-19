"""000028 audit (northstar_audit: durable tamper-evident audit-evidence sink).

Creates the process-global, append-only audit sink (LAW-14 / IMPL-004). Every meaningful action the
kernel command bus (and supplemental hooks) records is persisted here with its ``record_sha256`` so
the audit trail survives a restart. It is intentionally NOT tenant-partitioned (no RLS): the tenant,
when relevant, is carried in ``resource_type``/``resource_id`` on the record, and the sink may hold
cross-tenant platform/system evidence. Mirrors ``adapters.persistence_sqlalchemy.audit_tables``
exactly. Expand-only and reversible.

Revision ID: 000028
Revises: 000027
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000028"
down_revision: str | None = "000027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_audit"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.audit_record (
          evidence_id text PRIMARY KEY,
          event_type text NOT NULL,
          occurred_at text NOT NULL,
          actor_type text NOT NULL,
          actor_id text NOT NULL,
          actor_delegated_by text NULL,
          action text NOT NULL,
          outcome text NOT NULL,
          correlation_id text NOT NULL,
          resource_type text NULL,
          resource_id text NULL,
          decision_ref text NULL,
          reason_codes jsonb NOT NULL,
          record_sha256 text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS audit_record_correlation_idx "
        f"ON {_SCHEMA}.audit_record (correlation_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS audit_record_action_idx ON {_SCHEMA}.audit_record (action)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS audit_record_actor_idx ON {_SCHEMA}.audit_record (actor_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS audit_record_occurred_idx "
        f"ON {_SCHEMA}.audit_record (occurred_at)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.audit_record")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
