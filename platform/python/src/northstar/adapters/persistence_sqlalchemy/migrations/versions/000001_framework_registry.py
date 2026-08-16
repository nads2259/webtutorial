"""000001 framework registry (northstar_meta) + pgvector enablement.

Baseline migration for the kernel data owner. Mirrors
``spec/reference/one-touch/db/migrations/000001_framework_registry.sql``: creates the
``northstar_meta`` schema with the migration ledger and the module/capability/contract
registries, and enables the ``vector`` extension. Expand-only and reversible (downgrade drops
the schema and extension it created). Manifest: ``000001_framework_registry.json``.

Revision ID: 000001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS northstar_meta")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_meta.schema_migration (
          migration_id text PRIMARY KEY,
          module_id text NOT NULL,
          version text NOT NULL,
          checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
          applied_at timestamptz NOT NULL DEFAULT now(),
          applied_by text NOT NULL,
          execution_id uuid NOT NULL DEFAULT gen_random_uuid()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_meta.module_registry (
          module_id text PRIMARY KEY,
          version text NOT NULL,
          maturity text NOT NULL,
          manifest jsonb NOT NULL,
          manifest_sha256 text NOT NULL,
          enabled boolean NOT NULL DEFAULT false,
          activation_reason jsonb NOT NULL DEFAULT '{}'::jsonb,
          installed_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_meta.capability_registry (
          capability_id text PRIMARY KEY,
          module_id text NOT NULL REFERENCES northstar_meta.module_registry(module_id),
          contract_version text NOT NULL,
          command_handler text,
          query_handler text,
          policy_action text NOT NULL,
          enabled boolean NOT NULL DEFAULT true,
          UNIQUE (module_id, capability_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS northstar_meta.contract_registry (
          contract_id text NOT NULL,
          version text NOT NULL,
          kind text NOT NULL,
          schema_uri text NOT NULL,
          schema_sha256 text NOT NULL,
          compatibility text NOT NULL,
          registered_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (contract_id, version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS northstar_meta CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
