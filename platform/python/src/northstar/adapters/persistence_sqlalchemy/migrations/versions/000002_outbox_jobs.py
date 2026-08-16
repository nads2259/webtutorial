"""000002 runtime outbox + job queue (northstar_runtime).

Creates the transactional-outbox and durable job-queue tables for the kernel data owner.
Mirrors ``spec/reference/one-touch/db/migrations/000002_runtime_outbox_jobs.sql``: the
``northstar_runtime`` schema with ``outbox_event`` (events committed atomically with domain
state, dispatched at-least-once by the relay) and ``job`` (lease-based idempotent jobs), plus
the partial ready-indexes and the ``(job_type, idempotency_key)`` uniqueness that enforces job
idempotency. Expand-only and reversible (downgrade drops the schema and tables it created).
Manifest: ``000002_outbox_jobs.json``.

Revision ID: 000002
Revises: 000001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000002"
down_revision: str | None = "000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS northstar_runtime")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_runtime.outbox_event (
          event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          event_type text NOT NULL,
          event_version text NOT NULL,
          aggregate_type text NOT NULL,
          aggregate_id text NOT NULL,
          occurred_at timestamptz NOT NULL,
          payload jsonb NOT NULL,
          metadata jsonb NOT NULL,
          dispatched_at timestamptz,
          attempt_count integer NOT NULL DEFAULT 0,
          next_attempt_at timestamptz NOT NULL DEFAULT now(),
          last_error text
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_ready_idx "
        "ON northstar_runtime.outbox_event (next_attempt_at, occurred_at) "
        "WHERE dispatched_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_runtime.job (
          job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          job_type text NOT NULL,
          job_version text NOT NULL,
          queue text NOT NULL,
          idempotency_key text NOT NULL,
          payload jsonb NOT NULL,
          status text NOT NULL CHECK (status IN ('ready','leased','succeeded','failed','dead')),
          available_at timestamptz NOT NULL DEFAULT now(),
          lease_owner text,
          lease_expires_at timestamptz,
          attempt_count integer NOT NULL DEFAULT 0,
          max_attempts integer NOT NULL DEFAULT 10,
          last_error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (job_type, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS job_ready_idx "
        "ON northstar_runtime.job (queue, available_at) WHERE status = 'ready'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS northstar_runtime.job")
    op.execute("DROP TABLE IF EXISTS northstar_runtime.outbox_event")
    op.execute("DROP SCHEMA IF EXISTS northstar_runtime CASCADE")
