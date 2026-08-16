"""000010 research (northstar_research: workspaces/projects/documents/evidence/claims/refs) + RLS.

Creates the research module's owned schema and tables (docs/37, FR-RSH-001..006):

* ``workspace`` / ``research_project`` — tenant-scoped research containers (FR-RSH-001);
* ``research_document`` / ``research_revision`` — documents built on the shared knowledge
  typed-block tree, with IMMUTABLE published revisions carrying a ``content_hash`` (FR-RSH-002/006);
* ``evidence_record`` — provenance + stable version identity (FR-RSH-003);
* ``claim`` — claims linking to >=1 evidence record (the domain rejects zero-evidence claims,
  FR-RSH-003);
* ``dataset_ref`` / ``experiment_ref`` — ownership/license/classification/integrity/retention +
  version (FR-RSH-004).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY (tenant-scoped) research table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot
read another tenant's research even if an application predicate were ever omitted (rule 50,
defense-in-depth). Mirrors ``modules.research.adapters.tables`` exactly. Expand-only and reversible.
Manifest: ``000010_research.json`` (owner ``northstar.research``).

Revision ID: 000010
Revises: 000009
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000010"
down_revision: str | None = "000009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_research"
_TENANT_TABLES = (
    "workspace",
    "research_project",
    "research_document",
    "research_revision",
    "evidence_record",
    "claim",
    "dataset_ref",
    "experiment_ref",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.workspace (
          workspace_id text PRIMARY KEY,
          organization_id text NOT NULL,
          name text NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_workspace_org_idx "
        f"ON {_SCHEMA}.workspace (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.research_project (
          project_id text PRIMARY KEY,
          organization_id text NOT NULL,
          workspace_id text NOT NULL,
          title text NOT NULL,
          research_question text,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_project_org_idx "
        f"ON {_SCHEMA}.research_project (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_project_workspace_idx "
        f"ON {_SCHEMA}.research_project (workspace_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.research_document (
          document_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          title text NOT NULL,
          status text NOT NULL,
          content_tree jsonb NOT NULL,
          latest_revision_id text
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_document_org_idx "
        f"ON {_SCHEMA}.research_document (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_document_project_idx "
        f"ON {_SCHEMA}.research_document (project_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.research_revision (
          revision_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_id text NOT NULL,
          parent_revision_id text,
          title text NOT NULL,
          status text NOT NULL,
          content_tree jsonb NOT NULL,
          content_hash text NOT NULL,
          created_by jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_revision_org_idx "
        f"ON {_SCHEMA}.research_revision (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_revision_document_idx "
        f"ON {_SCHEMA}.research_revision (document_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.evidence_record (
          evidence_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_id text NOT NULL,
          kind text NOT NULL,
          excerpt text NOT NULL,
          version_hash text NOT NULL,
          object_id text,
          revision_id text,
          block_id text,
          chunk_id text,
          source_uri text,
          verified boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_evidence_org_idx "
        f"ON {_SCHEMA}.evidence_record (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_evidence_document_idx "
        f"ON {_SCHEMA}.evidence_record (document_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.claim (
          claim_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_id text NOT NULL,
          statement text NOT NULL,
          evidence_ids jsonb NOT NULL,
          confidence double precision,
          generated boolean NOT NULL DEFAULT false,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_claim_org_idx ON {_SCHEMA}.claim (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_claim_document_idx ON {_SCHEMA}.claim (document_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.dataset_ref (
          dataset_ref_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          name text NOT NULL,
          owner_id text NOT NULL,
          version text NOT NULL,
          integrity_hash text NOT NULL,
          license text NOT NULL,
          classification text NOT NULL,
          retention text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_dataset_org_idx "
        f"ON {_SCHEMA}.dataset_ref (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_dataset_project_idx "
        f"ON {_SCHEMA}.dataset_ref (project_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.experiment_ref (
          experiment_ref_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          name text NOT NULL,
          owner_id text NOT NULL,
          version text NOT NULL,
          reproducibility text NOT NULL,
          dataset_ref_ids jsonb NOT NULL,
          environment_digest text,
          seed text,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_experiment_org_idx "
        f"ON {_SCHEMA}.experiment_ref (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_experiment_project_idx "
        f"ON {_SCHEMA}.experiment_ref (project_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.experiment_ref")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.dataset_ref")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.claim")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.evidence_record")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.research_revision")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.research_document")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.research_project")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.workspace")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
