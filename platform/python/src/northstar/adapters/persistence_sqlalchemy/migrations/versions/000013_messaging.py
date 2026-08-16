"""000013 messaging (northstar_messaging: templates/campaigns/consent/suppression/deliveries) + RLS.

Creates the messaging module's owned schema and tables (docs/16, FR-MSG-001..007):

* ``template_version`` — versioned, IMMUTABLE templates; the ``(organization_id, template_id,
  version)`` primary key makes republishing a version a collision (FR-MSG-002);
* ``campaign`` — a campaign binding an exact template version, a safe segment spec, a
  recipient-time-zone-aware schedule and per-campaign tracking config (FR-MSG-001..004/007);
* ``consent_record`` / ``suppression_entry`` — the consent + suppression state checked before
  every marketing send so a suppressed/unsubscribed recipient is excluded (FR-MSG-005);
* ``delivery_receipt`` — the idempotency ledger; its ``(organization_id, campaign_id, recipient_id,
  idempotency_key)`` primary key means a re-submission collides instead of double-sending
  (FR-MSG-006).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY (tenant-scoped) messaging table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot
read another tenant's templates/campaigns/consent/suppression/deliveries even if an application
predicate were ever omitted (rule 50). Mirrors ``modules.messaging.adapters.tables`` exactly.
Expand-only and reversible. Manifest: ``000013_messaging.json`` (owner ``northstar.messaging``).

Revision ID: 000013
Revises: 000012
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000013"
down_revision: str | None = "000012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_messaging"
_TENANT_TABLES = (
    "template_version",
    "campaign",
    "consent_record",
    "suppression_entry",
    "delivery_receipt",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.template_version (
          organization_id text NOT NULL,
          template_id text NOT NULL,
          version integer NOT NULL,
          subject text NOT NULL,
          html_body text NOT NULL,
          text_body text NOT NULL,
          required_variables jsonb NOT NULL,
          content_hash text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, template_id, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_template_version_org_idx "
        f"ON {_SCHEMA}.template_version (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.campaign (
          organization_id text NOT NULL,
          campaign_id text NOT NULL,
          name text NOT NULL,
          message_class text NOT NULL,
          template_id text NOT NULL,
          template_version integer NOT NULL,
          channel text NOT NULL,
          purpose text NOT NULL,
          segment jsonb NOT NULL,
          schedule jsonb NOT NULL,
          open_tracking boolean NOT NULL DEFAULT false,
          click_tracking boolean NOT NULL DEFAULT false,
          status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, campaign_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_campaign_org_idx "
        f"ON {_SCHEMA}.campaign (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.consent_record (
          organization_id text NOT NULL,
          recipient_id text NOT NULL,
          channel text NOT NULL,
          purpose text NOT NULL,
          consented boolean NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, recipient_id, channel, purpose)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_consent_org_idx "
        f"ON {_SCHEMA}.consent_record (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.suppression_entry (
          organization_id text NOT NULL,
          recipient_id text NOT NULL,
          reason text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, recipient_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_suppression_org_idx "
        f"ON {_SCHEMA}.suppression_entry (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.delivery_receipt (
          organization_id text NOT NULL,
          campaign_id text NOT NULL,
          recipient_id text NOT NULL,
          idempotency_key text NOT NULL,
          provider_message_id text NOT NULL,
          status text NOT NULL,
          send_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, campaign_id, recipient_id, idempotency_key)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_delivery_org_idx "
        f"ON {_SCHEMA}.delivery_receipt (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS messaging_delivery_campaign_idx "
        f"ON {_SCHEMA}.delivery_receipt (campaign_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.delivery_receipt")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.suppression_entry")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.consent_record")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.campaign")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.template_version")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
