"""000016 learning & assessment (northstar_learning schema) + forced RLS.

Creates the learning module's owned schema and tables (docs/04 §5, FR-LRN-001..007):

* ``domain`` / ``learning_path`` / ``course`` — the learning design hierarchy; a course's
  ``sections`` (jsonb) compose PUBLISHED knowledge revisions + stable block ids (FR-LRN-001) and
  ``published`` gates learner availability;
* ``progress`` — the module's OWN progress state with a stable ``resume`` position, keyed by
  ``(organization_id, subject_id, course_id)`` — NEVER derived from analytics events (FR-LRN-002);
* ``overlay`` — private bookmarks/notes/highlights anchored at a stable position (FR-LRN-003);
* ``assessment_item`` — versioned items keyed by ``(organization_id, item_id, version)`` with a
  ``content_hash`` and a ``sealed`` flag that makes a version used in a scored attempt IMMUTABLE
  (FR-LRN-004);
* ``attempt`` — scored attempts (auditable completion evidence);
* ``completion_rule`` / ``credential`` — EXPLICIT completion rules and the verifiable credentials
  derived from them over auditable evidence (FR-LRN-005);
* ``profile_feature`` — the inferred-profile inventory a learner can inspect/correct/reset
  (EVAL-PRIV-004).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY table with a tenant-isolation policy
keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot read another tenant's rows
even if a predicate were ever omitted (rule 50). Mirrors ``learning.adapters.tables`` exactly.
Expand-only and reversible. Manifest: ``000016_learning.json``.

Revision ID: 000016
Revises: 000015
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000016"
down_revision: str | None = "000015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEARNING_SCHEMA = "northstar_learning"

_LEARNING_TABLES = (
    "domain",
    "learning_path",
    "course",
    "progress",
    "overlay",
    "assessment_item",
    "attempt",
    "completion_rule",
    "credential",
    "profile_feature",
)


def _upgrade_learning() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_LEARNING_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.domain (
          organization_id text NOT NULL,
          domain_id text NOT NULL,
          title text NOT NULL,
          slug text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, domain_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_domain_org_idx "
        f"ON {_LEARNING_SCHEMA}.domain (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.learning_path (
          organization_id text NOT NULL,
          path_id text NOT NULL,
          domain_id text NOT NULL,
          title text NOT NULL,
          course_ids jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, path_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_path_org_idx "
        f"ON {_LEARNING_SCHEMA}.learning_path (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.course (
          organization_id text NOT NULL,
          course_id text NOT NULL,
          domain_id text NOT NULL,
          path_id text,
          title text NOT NULL,
          sections jsonb NOT NULL,
          published boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, course_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_course_org_idx "
        f"ON {_LEARNING_SCHEMA}.course (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.progress (
          organization_id text NOT NULL,
          subject_id text NOT NULL,
          course_id text NOT NULL,
          resume jsonb NOT NULL,
          modality text NOT NULL,
          completed_sections jsonb NOT NULL,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, subject_id, course_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_progress_org_idx "
        f"ON {_LEARNING_SCHEMA}.progress (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_progress_subject_idx "
        f"ON {_LEARNING_SCHEMA}.progress (organization_id, subject_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.overlay (
          organization_id text NOT NULL,
          overlay_id text NOT NULL,
          subject_id text NOT NULL,
          course_id text NOT NULL,
          section_id text NOT NULL,
          block_id text NOT NULL,
          kind text NOT NULL,
          body text NOT NULL,
          quote text,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, overlay_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_overlay_org_idx "
        f"ON {_LEARNING_SCHEMA}.overlay (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_overlay_owner_idx "
        f"ON {_LEARNING_SCHEMA}.overlay (organization_id, subject_id, course_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.assessment_item (
          organization_id text NOT NULL,
          item_id text NOT NULL,
          version text NOT NULL,
          kind text NOT NULL,
          prompt text NOT NULL,
          answer_key jsonb NOT NULL,
          choices jsonb NOT NULL,
          points integer NOT NULL,
          pass_ratio double precision NOT NULL,
          max_attempts integer NOT NULL,
          accommodations jsonb NOT NULL,
          content_hash text NOT NULL,
          sealed boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, item_id, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_item_org_idx "
        f"ON {_LEARNING_SCHEMA}.assessment_item (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.attempt (
          organization_id text NOT NULL,
          attempt_id text NOT NULL,
          item_id text NOT NULL,
          item_version text NOT NULL,
          subject_id text NOT NULL,
          responses jsonb NOT NULL,
          raw integer NOT NULL,
          max integer NOT NULL,
          passed boolean NOT NULL,
          feedback text NOT NULL,
          accommodations jsonb NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (organization_id, attempt_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_attempt_org_idx "
        f"ON {_LEARNING_SCHEMA}.attempt (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_attempt_subject_idx "
        f"ON {_LEARNING_SCHEMA}.attempt (organization_id, subject_id, item_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.completion_rule (
          organization_id text NOT NULL,
          rule_id text NOT NULL,
          course_id text NOT NULL,
          required_section_ids jsonb NOT NULL,
          required_item_ids jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organization_id, rule_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_rule_org_idx "
        f"ON {_LEARNING_SCHEMA}.completion_rule (organization_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.credential (
          organization_id text NOT NULL,
          credential_id text NOT NULL,
          subject_id text NOT NULL,
          course_id text NOT NULL,
          rule_id text NOT NULL,
          evidence jsonb NOT NULL,
          verification_hash text NOT NULL,
          issued_at timestamptz,
          PRIMARY KEY (organization_id, credential_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_credential_org_idx "
        f"ON {_LEARNING_SCHEMA}.credential (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_credential_subject_idx "
        f"ON {_LEARNING_SCHEMA}.credential (organization_id, subject_id, course_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEARNING_SCHEMA}.profile_feature (
          organization_id text NOT NULL,
          subject_id text NOT NULL,
          name text NOT NULL,
          value text NOT NULL,
          inferred boolean NOT NULL,
          source text NOT NULL,
          updated_at timestamptz,
          PRIMARY KEY (organization_id, subject_id, name)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS learning_profile_org_idx "
        f"ON {_LEARNING_SCHEMA}.profile_feature (organization_id)"
    )


def upgrade() -> None:
    _upgrade_learning()

    connection = op.get_bind()
    for table in _LEARNING_TABLES:
        apply_tenant_rls(
            connection, schema=_LEARNING_SCHEMA, table=table, tenant_column="organization_id"
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_LEARNING_TABLES):
        drop_tenant_rls(connection, schema=_LEARNING_SCHEMA, table=table)

    for table in reversed(_LEARNING_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {_LEARNING_SCHEMA}.{table}")
    op.execute(f"DROP SCHEMA IF EXISTS {_LEARNING_SCHEMA} CASCADE")
