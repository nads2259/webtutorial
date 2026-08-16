"""000021 enterprise (northstar_enterprise: federation_mapping, provisioning_record) + forced RLS.

Creates the enterprise module's owned schema and tables in ``northstar_enterprise`` (FR-IDN-006,
FR-LRN-008; EVAL-IDN-005, EVAL-INT-001). ``enterprise_federation_mapping`` holds the deterministic
linkage from a verified external identity ``(issuer, external_subject)`` to a Northstar
``subject_id``/``user_id`` in a tenant (a UNIQUE ``(organization_id, issuer, external_subject)``
guarantees one mapping per external identity — the subject/user themselves live in the identity
module, never forked here). ``enterprise_provisioning_record`` holds SCIM-shaped user/group records
with an ``active`` flag (a UNIQUE ``(organization_id, external_id)`` makes provisioning idempotent);
deprovisioning flips ``active`` to false and stamps ``deactivated_at``. PostgreSQL Row-Level
Security is enabled + FORCED on both tenant-scoped tables with a tenant-isolation policy keyed to
the tenant GUC (``northstar.tenant_id``), so a non-superuser role cannot read another tenant's rows
even if a predicate were omitted (rule 50). Mirrors ``modules.enterprise.adapters.tables`` exactly.
Expand-only and reversible (downgrade drops the policies, tables and schema it created). Manifest:
``000021_enterprise.json``.

Revision ID: 000021
Revises: 000020
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000021"
down_revision: str | None = "000020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_enterprise"
_TENANT_TABLES = ("enterprise_federation_mapping", "enterprise_provisioning_record")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.enterprise_federation_mapping (
          mapping_id text PRIMARY KEY,
          organization_id text NOT NULL,
          issuer text NOT NULL,
          external_subject text NOT NULL,
          subject_id text NOT NULL,
          user_id text NOT NULL,
          linked_at timestamptz NOT NULL,
          CONSTRAINT enterprise_federation_mapping_identity_uq
            UNIQUE (organization_id, issuer, external_subject)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS enterprise_federation_mapping_org_idx "
        f"ON {_SCHEMA}.enterprise_federation_mapping (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.enterprise_provisioning_record (
          record_id text PRIMARY KEY,
          organization_id text NOT NULL,
          resource_type text NOT NULL,
          external_id text NOT NULL,
          active boolean NOT NULL,
          subject_id text,
          display_name text,
          email text,
          members jsonb NOT NULL,
          provisioned_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL,
          deactivated_at timestamptz,
          CONSTRAINT enterprise_provisioning_record_external_uq
            UNIQUE (organization_id, external_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS enterprise_provisioning_record_org_idx "
        f"ON {_SCHEMA}.enterprise_provisioning_record (organization_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.enterprise_provisioning_record")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.enterprise_federation_mapping")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
