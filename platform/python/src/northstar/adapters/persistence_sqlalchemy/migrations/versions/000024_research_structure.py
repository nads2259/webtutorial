"""000024 research structure (questions/hypotheses/methods/roles + doc blocks/sim-link/review).

Extends the research module's owned ``northstar_research`` schema with the project-structure and
document-journey tables that close GATE-RESEARCH-GA's buildable blockers (docs/37, FR-RSH-001/002):

* ``research_question`` / ``hypothesis`` / ``research_method`` — a project's questions, the
  hypotheses that answer them (``hypothesis.question_id`` links to a question) and its methods
  (EVAL-RSH-001);
* ``project_membership`` — role-scoped contributor membership, deny-by-default authorization source
  (EVAL-RSH-001, LAW-19);
* ``document_block`` — figure/table/literature-review typed blocks attached to a document, reusing
  the shared typed-block projection (EVAL-RSH-002);
* ``document_simulation_link`` — a document's link to a simulation recorded by IDENTITY only
  (EVAL-RSH-002, LAW-13);
* ``document_review`` / ``review_event`` — the peer-review state machine and its immutable, audited
  transition log (EVAL-RSH-002, LAW-14).

PostgreSQL Row-Level Security is enabled + FORCED on every new (tenant-scoped) table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC (rule 50, defense-in-depth).
Mirrors ``modules.research.adapters.tables`` exactly. Expand-only and reversible.
Manifest: ``000024_research_structure.json`` (owner ``northstar.research``).

Revision ID: 000024
Revises: 000023
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000024"
down_revision: str | None = "000023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_research"
_TENANT_TABLES = (
    "research_question",
    "hypothesis",
    "research_method",
    "project_membership",
    "document_block",
    "document_simulation_link",
    "document_review",
    "review_event",
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.research_question (
          question_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          prompt text NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_question_org_idx "
        f"ON {_SCHEMA}.research_question (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_question_project_idx "
        f"ON {_SCHEMA}.research_question (project_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.hypothesis (
          hypothesis_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          question_id text NOT NULL,
          statement text NOT NULL,
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_hypothesis_org_idx "
        f"ON {_SCHEMA}.hypothesis (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_hypothesis_project_idx "
        f"ON {_SCHEMA}.hypothesis (project_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_hypothesis_question_idx "
        f"ON {_SCHEMA}.hypothesis (question_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.research_method (
          method_id text PRIMARY KEY,
          organization_id text NOT NULL,
          project_id text NOT NULL,
          name text NOT NULL,
          description text NOT NULL DEFAULT '',
          created_by text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_method_org_idx "
        f"ON {_SCHEMA}.research_method (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_method_project_idx "
        f"ON {_SCHEMA}.research_method (project_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.project_membership (
          organization_id text NOT NULL,
          project_id text NOT NULL,
          subject_id text NOT NULL,
          role text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (project_id, subject_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_membership_org_idx "
        f"ON {_SCHEMA}.project_membership (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_membership_project_idx "
        f"ON {_SCHEMA}.project_membership (project_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.document_block (
          block_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_id text NOT NULL,
          kind text NOT NULL,
          position integer NOT NULL DEFAULT 0,
          payload jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_docblock_org_idx "
        f"ON {_SCHEMA}.document_block (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_docblock_document_idx "
        f"ON {_SCHEMA}.document_block (document_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.document_simulation_link (
          document_id text PRIMARY KEY,
          organization_id text NOT NULL,
          simulation_id text NOT NULL,
          version text NOT NULL,
          content_hash text NOT NULL,
          linked_by text NOT NULL,
          linked_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_simlink_org_idx "
        f"ON {_SCHEMA}.document_simulation_link (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.document_review (
          review_id text PRIMARY KEY,
          organization_id text NOT NULL,
          document_id text NOT NULL UNIQUE,
          status text NOT NULL,
          authors jsonb NOT NULL,
          reviewers jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_review_org_idx "
        f"ON {_SCHEMA}.document_review (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_review_document_idx "
        f"ON {_SCHEMA}.document_review (document_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.review_event (
          event_id text PRIMARY KEY,
          organization_id text NOT NULL,
          review_id text NOT NULL,
          from_status text NOT NULL,
          to_status text NOT NULL,
          action text NOT NULL,
          actor text NOT NULL,
          note text,
          occurred_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_review_event_org_idx "
        f"ON {_SCHEMA}.review_event (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS research_review_event_review_idx "
        f"ON {_SCHEMA}.review_event (review_id)"
    )

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.review_event")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.document_review")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.document_simulation_link")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.document_block")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.project_membership")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.research_method")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.hypothesis")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.research_question")
