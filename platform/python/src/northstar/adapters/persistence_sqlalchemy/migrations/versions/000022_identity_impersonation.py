"""000022 identity impersonation/break-glass + FORCED RLS on the identity schema (FR-IDN-007/008).

Extends the identity data owner (``northstar_identity``) with the auditable session-mode workflow
tables and closes the verify_005 LOW finding that the identity schema relied on application-layer
tenant scoping only:

* ``impersonation_grant`` — a time-bounded, reasoned, (optionally) approved support-impersonation
  grant naming both the real operator and the impersonated subject (FR-IDN-007);
* ``break_glass_access`` — an exceptional, justified, time-bounded, high-severity break-glass access
  (FR-IDN-008);
* ``post_use_review`` — the mandatory post-use review a break-glass access auto-enqueues, which
  starts ``pending`` and must be resolved (FR-IDN-008).

All three are tenant-scoped by a NOT NULL ``tenant_scope`` and get PostgreSQL Row-Level Security
ENABLED + FORCED with a tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC. In the
migration the PRE-EXISTING identity tables that carry a ``tenant_scope`` column (``subject`` and
``session``) are ALTERED to ENABLE + FORCE the same tenant RLS, so a non-superuser role cannot read
another tenant's identity rows even if an application predicate were ever omitted (rule 50) — the
identity schema now matches every other module (verify_005). This is an additive, reversible ALTER;
migration 000003 is not edited. Mirrors ``modules.identity.adapters.tables``. Manifest:
``000022_identity_impersonation.json``.

Revision ID: 000022
Revises: 000021
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000022"
down_revision: str | None = "000021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_identity"

# New tenant-scoped tables created by this migration.
_NEW_TENANT_TABLES = ("impersonation_grant", "break_glass_access", "post_use_review")

# Pre-existing identity tables that carry ``tenant_scope`` and now get FORCED RLS (verify_005).
_EXISTING_TENANT_TABLES = ("subject", "session")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.impersonation_grant (
          grant_id text PRIMARY KEY,
          tenant_scope text NOT NULL,
          real_actor_id text NOT NULL,
          impersonated_subject_id text NOT NULL,
          reason text NOT NULL,
          started_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          approved_by text,
          ended_at timestamptz
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS impersonation_grant_tenant_idx "
        f"ON {_SCHEMA}.impersonation_grant (tenant_scope)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.break_glass_access (
          access_id text PRIMARY KEY,
          tenant_scope text NOT NULL,
          operator_id text NOT NULL,
          justification text NOT NULL,
          severity text NOT NULL,
          invoked_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          authorized_by text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS break_glass_access_tenant_idx "
        f"ON {_SCHEMA}.break_glass_access (tenant_scope)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.post_use_review (
          review_id text PRIMARY KEY,
          tenant_scope text NOT NULL,
          access_id text NOT NULL,
          status text NOT NULL,
          opened_at timestamptz NOT NULL,
          resolved_at timestamptz,
          resolved_by text,
          resolution text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS post_use_review_tenant_idx "
        f"ON {_SCHEMA}.post_use_review (tenant_scope)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS post_use_review_access_idx "
        f"ON {_SCHEMA}.post_use_review (tenant_scope, access_id)"
    )

    connection = op.get_bind()
    # Force tenant RLS on the new tables AND on the pre-existing tenant-scoped identity tables.
    for table in (*_NEW_TENANT_TABLES, *_EXISTING_TENANT_TABLES):
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="tenant_scope")


def downgrade() -> None:
    connection = op.get_bind()
    # Restore the pre-existing identity tables to their 000003 state (RLS disabled), then drop the
    # RLS + tables this migration created.
    for table in reversed(_EXISTING_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    for table in reversed(_NEW_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
        op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.{table}")
