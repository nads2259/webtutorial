"""000015 commerce + support (northstar_commerce + northstar_support schemas) + forced RLS.

Creates the commerce and support modules' owned schemas and tables (docs/29, FR-COM-001..005,
FR-SUP-001..003):

commerce (``northstar_commerce``):
* ``product`` / ``offer`` — the catalog; an offer composes free/paid/tier access and is versioned by
  ``(organization_id, offer_id, version)`` so a purchase references the exact accepted version;
* ``purchase`` — a purchase's lifecycle + the entitlement grant ids it fulfilled;
* ``payment_event`` — the idempotency ledger; ``(organization_id, event_id)`` makes a processed
  provider callback single-effect (FR-COM-003);
* ``refund`` — auditable refund records (FR-COM-004);
* ``entitlement_grant`` — the entitlement grants commerce issues for purchases (commerce owns its
  commercial data, LAW-13; the decision logic is REUSED from the entitlement engine, not
  re-implemented), revocable via ``revoked``/``revoked_at`` for refunds;
* ``ad_placement`` — advertising/sponsorship surfaces, always flagged ``disclosed`` (FR-COM-005).

support (``northstar_support``):
* ``support_case`` / ``support_message`` — governed cases with ownership + lifecycle; internal notes
  (``visibility='internal'``) are stored but excluded from the minimized staff projection;
* ``support_access_grant`` — audited, deny-by-default, TIME-BOUNDED elevated-access grants;
* ``support_access_log`` — tamper-evident record of every minimized/elevated read (incl. refused
  broad reads) — FR-SUP-003.

PostgreSQL Row-Level Security is enabled + FORCED on EVERY table with a tenant-isolation policy
keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot read another tenant's rows
even if an application predicate were ever omitted (rule 50). Mirrors the commerce/support
``adapters.tables`` exactly. Expand-only and reversible. Manifest: ``000015_commerce_support.json``.

Revision ID: 000015
Revises: 000014
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000015"
down_revision: str | None = "000014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMERCE_SCHEMA = "northstar_commerce"
_SUPPORT_SCHEMA = "northstar_support"

_COMMERCE_TABLES = (
    "product",
    "offer",
    "purchase",
    "payment_event",
    "refund",
    "entitlement_grant",
    "ad_placement",
)
_SUPPORT_TABLES = (
    "support_case",
    "support_message",
    "support_access_grant",
    "support_access_log",
)


def _upgrade_commerce() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_COMMERCE_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.product (
          organization_id text NOT NULL,
          product_id text NOT NULL,
          name text NOT NULL,
          kind text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, product_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_product_org_idx "
        f"ON {_COMMERCE_SCHEMA}.product (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.offer (
          organization_id text NOT NULL,
          offer_id text NOT NULL,
          version text NOT NULL,
          product_id text NOT NULL,
          status text NOT NULL,
          price jsonb NOT NULL,
          grants jsonb NOT NULL,
          eligibility jsonb NOT NULL,
          terms_version text NOT NULL,
          effective_from timestamptz,
          effective_until timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, offer_id, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_offer_org_idx "
        f"ON {_COMMERCE_SCHEMA}.offer (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.purchase (
          organization_id text NOT NULL,
          purchase_id text NOT NULL,
          offer_id text NOT NULL,
          offer_version text NOT NULL,
          product_id text NOT NULL,
          subject_id text NOT NULL,
          status text NOT NULL,
          grant_ids jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, purchase_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_purchase_org_idx "
        f"ON {_COMMERCE_SCHEMA}.purchase (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_purchase_subject_idx "
        f"ON {_COMMERCE_SCHEMA}.purchase (organization_id, subject_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.payment_event (
          organization_id text NOT NULL,
          event_id text NOT NULL,
          event_type text NOT NULL,
          purchase_id text NOT NULL,
          processed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, event_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_payment_event_org_idx "
        f"ON {_COMMERCE_SCHEMA}.payment_event (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.refund (
          organization_id text NOT NULL,
          refund_id text NOT NULL,
          purchase_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, refund_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_refund_org_idx "
        f"ON {_COMMERCE_SCHEMA}.refund (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.entitlement_grant (
          organization_id text NOT NULL,
          grant_id text NOT NULL,
          subject_id text NOT NULL,
          capability text NOT NULL,
          origin text NOT NULL,
          starts_at timestamptz NOT NULL,
          ends_at timestamptz,
          quota_limit integer,
          quota_used integer NOT NULL DEFAULT 0,
          quota_disposition text NOT NULL,
          revoked boolean NOT NULL DEFAULT false,
          revoked_at timestamptz,
          PRIMARY KEY (organization_id, grant_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_entitlement_grant_org_idx "
        f"ON {_COMMERCE_SCHEMA}.entitlement_grant (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_entitlement_grant_subject_idx "
        f"ON {_COMMERCE_SCHEMA}.entitlement_grant (organization_id, subject_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COMMERCE_SCHEMA}.ad_placement (
          organization_id text NOT NULL,
          placement_id text NOT NULL,
          kind text NOT NULL,
          disclosure_label text NOT NULL,
          disclosed boolean NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, placement_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS commerce_ad_placement_org_idx "
        f"ON {_COMMERCE_SCHEMA}.ad_placement (organization_id)"
    )


def _upgrade_support() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SUPPORT_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SUPPORT_SCHEMA}.support_case (
          organization_id text NOT NULL,
          case_id text NOT NULL,
          requester_id text NOT NULL,
          assignee_id text,
          status text NOT NULL,
          priority text NOT NULL,
          category text NOT NULL,
          subject text,
          audit_scope text NOT NULL,
          retention_policy text,
          related_resources jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, case_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_case_org_idx "
        f"ON {_SUPPORT_SCHEMA}.support_case (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_case_requester_idx "
        f"ON {_SUPPORT_SCHEMA}.support_case (organization_id, requester_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SUPPORT_SCHEMA}.support_message (
          organization_id text NOT NULL,
          message_id text NOT NULL,
          case_id text NOT NULL,
          author_type text NOT NULL,
          body_ref text NOT NULL,
          body text NOT NULL,
          visibility text NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (organization_id, message_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_message_org_idx "
        f"ON {_SUPPORT_SCHEMA}.support_message (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_message_case_idx "
        f"ON {_SUPPORT_SCHEMA}.support_message (organization_id, case_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SUPPORT_SCHEMA}.support_access_grant (
          organization_id text NOT NULL,
          grant_id text NOT NULL,
          case_id text NOT NULL,
          staff_id text NOT NULL,
          granted_by text NOT NULL,
          reason text NOT NULL,
          scope text NOT NULL,
          starts_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          revoked boolean NOT NULL DEFAULT false,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, grant_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_access_grant_org_idx "
        f"ON {_SUPPORT_SCHEMA}.support_access_grant (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_access_grant_lookup_idx "
        f"ON {_SUPPORT_SCHEMA}.support_access_grant (organization_id, case_id, staff_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SUPPORT_SCHEMA}.support_access_log (
          organization_id text NOT NULL,
          log_id text NOT NULL,
          case_id text NOT NULL,
          staff_id text NOT NULL,
          scope text NOT NULL,
          decision text NOT NULL,
          occurred_at timestamptz NOT NULL,
          PRIMARY KEY (organization_id, log_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_access_log_org_idx "
        f"ON {_SUPPORT_SCHEMA}.support_access_log (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS support_access_log_case_idx "
        f"ON {_SUPPORT_SCHEMA}.support_access_log (organization_id, case_id)"
    )


def upgrade() -> None:
    _upgrade_commerce()
    _upgrade_support()

    connection = op.get_bind()
    for table in _COMMERCE_TABLES:
        apply_tenant_rls(
            connection, schema=_COMMERCE_SCHEMA, table=table, tenant_column="organization_id"
        )
    for table in _SUPPORT_TABLES:
        apply_tenant_rls(
            connection, schema=_SUPPORT_SCHEMA, table=table, tenant_column="organization_id"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_SUPPORT_TABLES):
        drop_tenant_rls(connection, schema=_SUPPORT_SCHEMA, table=table)
    for table in reversed(_COMMERCE_TABLES):
        drop_tenant_rls(connection, schema=_COMMERCE_SCHEMA, table=table)

    for table in reversed(_SUPPORT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {_SUPPORT_SCHEMA}.{table}")
    op.execute(f"DROP SCHEMA IF EXISTS {_SUPPORT_SCHEMA} CASCADE")

    for table in reversed(_COMMERCE_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {_COMMERCE_SCHEMA}.{table}")
    op.execute(f"DROP SCHEMA IF EXISTS {_COMMERCE_SCHEMA} CASCADE")
