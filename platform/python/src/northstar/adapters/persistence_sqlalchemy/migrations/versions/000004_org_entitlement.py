"""000004 organization + entitlement (northstar_organization, northstar_entitlement) + RLS.

Creates the organization tenancy tree (``organization``/``workspace``/``team``/``membership`` in
``northstar_organization``) and entitlement grants (``entitlement_grant`` in
``northstar_entitlement``), then enables PostgreSQL Row-Level Security as defense-in-depth
(FR-POL-004): every tenant-scoped table gets ``FORCE ROW LEVEL SECURITY`` and a tenant-isolation
policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot read/insert/update
another tenant's rows even if an application predicate were omitted. Mirrors
``modules.organization.adapters.tables`` and ``modules.entitlement.adapters.tables`` exactly.
Expand-only and reversible (downgrade drops the policies, tables and schemas it created).
Manifest: ``000004_org_entitlement.json`` (owner ``northstar.organization``).

Revision ID: 000004
Revises: 000003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import (
    TENANT_GUC,
    apply_tenant_rls,
    drop_tenant_rls,
)

revision: str = "000004"
down_revision: str | None = "000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG_SCHEMA = "northstar_organization"
_ENT_SCHEMA = "northstar_entitlement"
_ORG_TENANT_TABLES = ("organization", "workspace", "team", "membership")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_ORG_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ORG_SCHEMA}.organization (
          organization_id text PRIMARY KEY,
          name text NOT NULL,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ORG_SCHEMA}.workspace (
          workspace_id text PRIMARY KEY,
          organization_id text NOT NULL,
          name text NOT NULL,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS workspace_org_idx ON {_ORG_SCHEMA}.workspace (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ORG_SCHEMA}.team (
          team_id text PRIMARY KEY,
          workspace_id text NOT NULL,
          organization_id text NOT NULL,
          name text NOT NULL,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS team_org_idx ON {_ORG_SCHEMA}.team (organization_id)")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ORG_SCHEMA}.membership (
          membership_id text PRIMARY KEY,
          subject_id text NOT NULL,
          organization_id text NOT NULL,
          roles jsonb NOT NULL,
          workspace_id text,
          team_id text,
          created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS membership_org_idx "
        f"ON {_ORG_SCHEMA}.membership (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS membership_subject_idx "
        f"ON {_ORG_SCHEMA}.membership (subject_id)"
    )

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_ENT_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ENT_SCHEMA}.entitlement_grant (
          grant_id text PRIMARY KEY,
          subject_id text NOT NULL,
          organization_id text,
          capability text NOT NULL,
          origin text NOT NULL,
          starts_at timestamptz NOT NULL,
          ends_at timestamptz,
          quota_limit integer,
          quota_used integer NOT NULL DEFAULT 0,
          quota_disposition text NOT NULL,
          revoked boolean NOT NULL DEFAULT false,
          revoked_at timestamptz
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS entitlement_grant_subject_idx "
        f"ON {_ENT_SCHEMA}.entitlement_grant (subject_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS entitlement_grant_org_idx "
        f"ON {_ENT_SCHEMA}.entitlement_grant (organization_id)"
    )

    # RLS defense-in-depth (FR-POL-004): tenant-scoped org tables isolate by organization_id.
    connection = op.get_bind()
    for table in _ORG_TENANT_TABLES:
        apply_tenant_rls(
            connection, schema=_ORG_SCHEMA, table=table, tenant_column="organization_id"
        )

    # Entitlement grants may be individual (organization_id NULL) or org-scoped; org-scoped rows
    # isolate by tenant, individual rows remain visible to their subject's queries.
    predicate = (
        f"organization_id IS NULL OR organization_id = current_setting('{TENANT_GUC}', true)"
    )
    op.execute(f"ALTER TABLE {_ENT_SCHEMA}.entitlement_grant ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ENT_SCHEMA}.entitlement_grant FORCE ROW LEVEL SECURITY")
    op.execute(
        f"DROP POLICY IF EXISTS entitlement_grant_tenant_isolation "
        f"ON {_ENT_SCHEMA}.entitlement_grant"
    )
    op.execute(
        f"CREATE POLICY entitlement_grant_tenant_isolation ON {_ENT_SCHEMA}.entitlement_grant "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    connection = op.get_bind()
    op.execute(
        f"DROP POLICY IF EXISTS entitlement_grant_tenant_isolation "
        f"ON {_ENT_SCHEMA}.entitlement_grant"
    )
    op.execute(f"DROP TABLE IF EXISTS {_ENT_SCHEMA}.entitlement_grant")
    op.execute(f"DROP SCHEMA IF EXISTS {_ENT_SCHEMA} CASCADE")

    for table in reversed(_ORG_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_ORG_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_ORG_SCHEMA}.membership")
    op.execute(f"DROP TABLE IF EXISTS {_ORG_SCHEMA}.team")
    op.execute(f"DROP TABLE IF EXISTS {_ORG_SCHEMA}.workspace")
    op.execute(f"DROP TABLE IF EXISTS {_ORG_SCHEMA}.organization")
    op.execute(f"DROP SCHEMA IF EXISTS {_ORG_SCHEMA} CASCADE")
