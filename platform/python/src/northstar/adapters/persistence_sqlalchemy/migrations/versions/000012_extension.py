"""000012 extension (northstar_extension: installations/catalog-listings/theme-applications) + RLS.

Creates the extension module's owned schema and tables (docs/14, FR-EXT-001..008):

* ``extension_installation`` — installed extensions with their assigned (review/deployment-policy)
  trust tier, granted permissions, signed package digest and enabled/disabled lifecycle state; the
  source of truth the in-process dispatch guard consults so a disabled/uninstalled extension can no
  longer execute (FR-EXT-004/005);
* ``catalog_listing`` — public catalog entries that require a verified publisher (FR-EXT-008);
* ``theme_application`` — the semantic-token presentation a tenant applied; a theme changes only
  presentation and never authorization (FR-EXT-006).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY (tenant-scoped) extension table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot
read another tenant's installations/listings/themes even if an application predicate were ever
omitted
(rule 50, defense-in-depth). Mirrors ``modules.extension.adapters.tables`` exactly. Expand-only and
reversible. Manifest: ``000012_extension.json`` (owner ``northstar.extension``).

Revision ID: 000012
Revises: 000011
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000012"
down_revision: str | None = "000011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_extension"
_TENANT_TABLES = (
    "extension_installation",
    "catalog_listing",
    "theme_application",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.extension_installation (
          organization_id text NOT NULL,
          extension_id text NOT NULL,
          version text NOT NULL,
          publisher_id text NOT NULL,
          extension_type text NOT NULL,
          required_trust_tier text NOT NULL,
          granted_trust_tier text NOT NULL,
          permissions jsonb NOT NULL,
          package_digest text NOT NULL,
          uninstall_policy text NOT NULL,
          state text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, extension_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS extension_installation_org_idx "
        f"ON {_SCHEMA}.extension_installation (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.catalog_listing (
          organization_id text NOT NULL,
          extension_id text NOT NULL,
          version text NOT NULL,
          publisher_id text NOT NULL,
          verified boolean NOT NULL DEFAULT false,
          permissions jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, extension_id, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS extension_catalog_listing_org_idx "
        f"ON {_SCHEMA}.catalog_listing (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.theme_application (
          organization_id text NOT NULL,
          theme_id text NOT NULL,
          version text NOT NULL,
          presentation jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, theme_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS extension_theme_application_org_idx "
        f"ON {_SCHEMA}.theme_application (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.theme_application")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.catalog_listing")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.extension_installation")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
