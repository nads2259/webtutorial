"""000009 ai (northstar_ai: prompt_package/ai_memory/ai_trace) + RLS.

Creates the AI module's owned schema and tables (docs/10, ARCH-009):

* ``prompt_package`` — the IMMUTABLE versioned prompt registry (global config, no tenant column):
  a ``(package_id, version)`` is inserted once and never updated (FR-AI-002);
* ``ai_memory`` — purpose-limited, deletable per-owner memory, tenant-scoped (FR-AI-006);
* ``ai_trace`` — per-interaction provenance (model/provider/prompt/tools/cost), tenant-scoped
  (FR-AI-009).

PostgreSQL Row-Level Security is enabled + FORCED on the tenant-scoped ``ai_memory`` and
``ai_trace`` tables with a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a
non-superuser role cannot read another tenant's memory/traces even if an application predicate were
ever omitted (rule 50, defense-in-depth). Mirrors ``modules.ai.adapters.tables`` exactly.
Expand-only and reversible. Manifest: ``000009_ai.json`` (owner ``northstar.ai``).

Revision ID: 000009
Revises: 000008
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000009"
down_revision: str | None = "000008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_ai"
_TENANT_TABLES = (
    "ai_memory",
    "ai_trace",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.prompt_package (
          package_id text NOT NULL,
          version text NOT NULL,
          actor_profile text NOT NULL,
          purpose text NOT NULL,
          system_instruction text NOT NULL,
          developer_instructions jsonb NOT NULL,
          declared_tools jsonb NOT NULL,
          retrieval_profile text,
          memory_policy text NOT NULL,
          evaluation_suite text NOT NULL,
          status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (package_id, version)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.ai_memory (
          memory_id text PRIMARY KEY,
          organization_id text NOT NULL,
          owner_id text NOT NULL,
          memory_class text NOT NULL,
          purpose text NOT NULL,
          classification text NOT NULL,
          content text NOT NULL,
          retention text NOT NULL,
          inferred boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ai_memory_owner_idx "
        f"ON {_SCHEMA}.ai_memory (organization_id, owner_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.ai_trace (
          trace_id text PRIMARY KEY,
          organization_id text NOT NULL,
          actor_id text NOT NULL,
          actor_profile text NOT NULL,
          provider text NOT NULL,
          model text NOT NULL,
          prompt_package text NOT NULL,
          input_tokens integer NOT NULL,
          output_tokens integer NOT NULL,
          cost_units double precision NOT NULL,
          tool_calls jsonb NOT NULL,
          citations_valid integer NOT NULL,
          citations_rejected integer NOT NULL,
          refused boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ai_trace_org_idx ON {_SCHEMA}.ai_trace (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.ai_trace")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.ai_memory")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.prompt_package")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
