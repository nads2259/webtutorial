"""000017 privacy & data-subject rights (northstar_privacy schema) + forced RLS.

Creates the privacy module's owned schema and tables (docs/09, NFR-PRV-001..005):

* ``data_field`` — the personal-data CATALOG; every field declares a ``purpose``, a
  ``lawful_basis`` and a ``retention_days`` under a ``data_class`` (EVAL-PRIV-001);
* ``consent_record`` — the VERSIONED, APPEND-ONLY consent history keyed by
  ``(organization_id, record_id)`` with a monotonically increasing ``version`` per
  ``(subject_id, purpose)`` — a new decision inserts a new immutable row (EVAL-PRIV-002);
* ``rights_request`` — the access/export/erase request lifecycle records (EVAL-PRIV-003).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY table with a tenant-isolation policy
keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot read another tenant's rows
even if a predicate were ever omitted (rule 50). Mirrors ``privacy.adapters.tables`` exactly.
Expand-only and reversible. Manifest: ``000017_privacy.json``.

Revision ID: 000017
Revises: 000016
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000017"
down_revision: str | None = "000016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRIVACY_SCHEMA = "northstar_privacy"

_PRIVACY_TABLES = (
    "data_field",
    "consent_record",
    "rights_request",
)


def _upgrade_privacy() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_PRIVACY_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_PRIVACY_SCHEMA}.data_field (
          organization_id text NOT NULL,
          field_id text NOT NULL,
          module_id text NOT NULL,
          name text NOT NULL,
          purpose text NOT NULL,
          lawful_basis text NOT NULL,
          data_class text NOT NULL,
          retention_days integer NOT NULL,
          description text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, field_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS privacy_data_field_org_idx "
        f"ON {_PRIVACY_SCHEMA}.data_field (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_PRIVACY_SCHEMA}.consent_record (
          organization_id text NOT NULL,
          record_id text NOT NULL,
          subject_id text NOT NULL,
          purpose text NOT NULL,
          category text NOT NULL,
          state text NOT NULL,
          lawful_basis text NOT NULL,
          version integer NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, record_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS privacy_consent_org_idx "
        f"ON {_PRIVACY_SCHEMA}.consent_record (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS privacy_consent_subject_idx "
        f"ON {_PRIVACY_SCHEMA}.consent_record (organization_id, subject_id, purpose)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_PRIVACY_SCHEMA}.rights_request (
          organization_id text NOT NULL,
          request_id text NOT NULL,
          subject_id text NOT NULL,
          requested_by text NOT NULL,
          rights_type text NOT NULL,
          status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          PRIMARY KEY (organization_id, request_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS privacy_request_org_idx "
        f"ON {_PRIVACY_SCHEMA}.rights_request (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS privacy_request_subject_idx "
        f"ON {_PRIVACY_SCHEMA}.rights_request (organization_id, subject_id)"
    )


def upgrade() -> None:
    _upgrade_privacy()

    connection = op.get_bind()
    for table in _PRIVACY_TABLES:
        apply_tenant_rls(
            connection, schema=_PRIVACY_SCHEMA, table=table, tenant_column="organization_id"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_PRIVACY_TABLES):
        drop_tenant_rls(connection, schema=_PRIVACY_SCHEMA, table=table)

    for table in reversed(_PRIVACY_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {_PRIVACY_SCHEMA}.{table}")
    op.execute(f"DROP SCHEMA IF EXISTS {_PRIVACY_SCHEMA} CASCADE")
