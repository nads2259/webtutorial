"""000014 analytics (northstar_analytics: event_definition/event/identity_stitch) + forced RLS.

Creates the analytics module's owned schema and tables (docs/17, FR-ANL-001..007):

* ``event_definition`` — the purpose-governed event CATALOG; the ``(organization_id, event_name,
  version)`` primary key makes a registered event type immutable, so re-registering a version is a
  collision (FR-ANL-003);
* ``event`` — the AUTHORITATIVE first-party analytics records the ingestion pipeline persists after
  validating each event against its catalog definition (FR-ANL-001/002/007);
* ``identity_stitch`` — the explicit, consent-backed anonymous↔user links produced only with the
  required consent (FR-ANL-004).

GA4-derived figures are deliberately NOT persisted here: they are optional, non-authoritative
imports carrying source freshness + mapping (docs/17 §9, FR-ANL-006).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY analytics table with a tenant-isolation
policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot read another
tenant's catalog/events/stitches even if an application predicate were ever omitted (rule 50).
Mirrors
``modules.analytics.adapters.tables`` exactly. Expand-only and reversible. Manifest:
``000014_analytics.json`` (owner ``northstar.analytics``).

Revision ID: 000014
Revises: 000013
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000014"
down_revision: str | None = "000013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_analytics"
_TENANT_TABLES = (
    "event_definition",
    "event",
    "identity_stitch",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.event_definition (
          organization_id text NOT NULL,
          event_name text NOT NULL,
          version integer NOT NULL,
          owner text NOT NULL,
          purpose text NOT NULL,
          consent_category text NOT NULL,
          retention_days integer NOT NULL,
          destinations jsonb NOT NULL,
          properties jsonb NOT NULL,
          trigger text,
          sampling text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, event_name, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS analytics_event_definition_org_idx "
        f"ON {_SCHEMA}.event_definition (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.event (
          organization_id text NOT NULL,
          event_id text NOT NULL,
          event_name text NOT NULL,
          event_version integer NOT NULL,
          actor_type text NOT NULL,
          actor_id text NOT NULL,
          anonymous_id text,
          occurred_at timestamptz NOT NULL,
          properties jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, event_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS analytics_event_org_idx ON {_SCHEMA}.event (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS analytics_event_name_idx "
        f"ON {_SCHEMA}.event (organization_id, event_name)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.identity_stitch (
          organization_id text NOT NULL,
          anonymous_id text NOT NULL,
          user_id text NOT NULL,
          consent_category text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, anonymous_id, user_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS analytics_identity_stitch_org_idx "
        f"ON {_SCHEMA}.identity_stitch (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.identity_stitch")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.event")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.event_definition")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
