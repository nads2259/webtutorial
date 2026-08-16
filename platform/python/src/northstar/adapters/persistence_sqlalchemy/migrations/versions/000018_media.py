"""000018 media (northstar_media: media_asset) + forced tenant RLS.

Creates the media module's owned schema and table (``media_asset``) in ``northstar_media``
(FR-CNT-009/010, NFR-A11Y-003). Each asset records its type (video/audio/image), the VALIDATED
content type and stored blob reference (evidence the bytes passed the H02 upload validator) and its
accessible alternatives (a ``transcript`` JSONB, a ``captions`` JSONB array of timecode-addressable
cue tracks, ``alt_text`` and a ``decorative`` flag). PostgreSQL Row-Level Security is enabled +
FORCED on the tenant-scoped table with a tenant-isolation policy keyed to the tenant GUC
(``northstar.tenant_id``), so a non-superuser role cannot read another tenant's rows even if a
predicate were omitted
(rule 50). Mirrors ``modules.media.adapters.tables`` exactly. Expand-only and reversible (downgrade
drops the policy, table and schema it created). Manifest: ``000018_media.json``.

Revision ID: 000018
Revises: 000017
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000018"
down_revision: str | None = "000017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_media"
_TENANT_TABLES = ("media_asset",)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.media_asset (
          asset_id text PRIMARY KEY,
          organization_id text NOT NULL,
          media_type text NOT NULL,
          content_type text NOT NULL,
          blob_ref text NOT NULL,
          byte_size integer NOT NULL,
          title text,
          state text NOT NULL,
          transcript jsonb,
          captions jsonb NOT NULL,
          alt_text text,
          decorative boolean NOT NULL,
          duration_seconds double precision,
          created_by jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          policy_decision_id text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS media_asset_org_idx ON {_SCHEMA}.media_asset (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS media_asset_state_idx "
        f"ON {_SCHEMA}.media_asset (organization_id, state)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.media_asset")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
