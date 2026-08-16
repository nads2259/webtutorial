"""000019 moderation (northstar_moderation: moderation_case, moderation_event) + forced tenant RLS.

Creates the moderation module's owned schema and tables in ``northstar_moderation`` (FR-ANN-007,
EVAL-MOD-001). ``moderation_case`` holds the authoritative case aggregate for a piece of reportable
content (its target + author, the coalesced ``reports`` JSONB, the deterministic lifecycle
``state``, and the ``decision``/``enforcement``/``appeal`` JSONB projections). ``moderation_event``
is the append-only, tamper-evident lifecycle trail (actor + from/to state + rationale) that
evidences every authorized transition and a reversed enforcement (LAW-14). PostgreSQL Row-Level
Security is enabled + FORCED on both tenant-scoped tables with a tenant-isolation policy keyed to
the tenant GUC (``northstar.tenant_id``), so a non-superuser role cannot read another tenant's rows
even if a predicate were omitted (rule 50). Mirrors ``modules.moderation.adapters.tables`` exactly.
Expand-only and reversible (downgrade drops the policies, tables and schema it created). Manifest:
``000019_moderation.json``.

Revision ID: 000019
Revises: 000018
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000019"
down_revision: str | None = "000018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_moderation"
_TENANT_TABLES = ("moderation_case", "moderation_event")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.moderation_case (
          case_id text PRIMARY KEY,
          organization_id text NOT NULL,
          content_type text NOT NULL,
          content_id text NOT NULL,
          author_id text NOT NULL,
          state text NOT NULL,
          reports jsonb NOT NULL,
          assignee_id text,
          decision jsonb,
          enforcement jsonb,
          appeal jsonb,
          created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS moderation_case_org_idx "
        f"ON {_SCHEMA}.moderation_case (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS moderation_case_target_idx "
        f"ON {_SCHEMA}.moderation_case (organization_id, content_type, content_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS moderation_case_state_idx "
        f"ON {_SCHEMA}.moderation_case (organization_id, state)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.moderation_event (
          event_id text PRIMARY KEY,
          case_id text NOT NULL,
          organization_id text NOT NULL,
          action text NOT NULL,
          from_state text,
          to_state text NOT NULL,
          actor jsonb NOT NULL,
          rationale text,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS moderation_event_org_idx "
        f"ON {_SCHEMA}.moderation_event (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS moderation_event_case_idx "
        f"ON {_SCHEMA}.moderation_event (organization_id, case_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.moderation_event")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.moderation_case")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
