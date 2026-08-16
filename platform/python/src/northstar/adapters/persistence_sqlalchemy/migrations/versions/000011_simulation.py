"""000011 simulation (northstar_simulation: definitions/trust-tiers/run-evidence/scores) + RLS.

Creates the simulation module's owned schema and tables (docs/15, FR-SIM-001..008):

* ``simulation_definition`` — versioned, schema-valid definitions, IMMUTABLE once published, plus a
  restricted ``scoring_key`` column that is NEVER projected into the definition document, the
  sandbox or the AI coach's context (FR-SIM-001/007);
* ``trust_tier`` — the runtime trust tiers the Governance Studio approves per tenant (FR-SIM-008);
* ``run_evidence`` — immutable, hash-chained run evidence (definition/runtime versions, inputs,
  actions, outcome) that is tamper-evident (FR-SIM-005);
* ``score`` — deterministic, replayable run scores (FR-SIM-006).

PostgreSQL Row-Level Security is enabled + FORCED on EVERY (tenant-scoped) simulation table with a
tenant-isolation policy keyed to the ``northstar.tenant_id`` GUC, so a non-superuser role cannot
read another tenant's simulations/evidence even if an application predicate were ever omitted
(rule 50, defense-in-depth). Mirrors ``modules.simulation.adapters.tables`` exactly. Expand-only and
reversible. Manifest: ``000011_simulation.json`` (owner ``northstar.simulation``).

Revision ID: 000011
Revises: 000010
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from northstar.adapters.persistence_sqlalchemy.tenancy import apply_tenant_rls, drop_tenant_rls

revision: str = "000011"
down_revision: str | None = "000010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "northstar_simulation"
_TENANT_TABLES = (
    "simulation_definition",
    "trust_tier",
    "run_evidence",
    "score",
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.simulation_definition (
          simulation_id text NOT NULL,
          version text NOT NULL,
          organization_id text NOT NULL,
          document jsonb NOT NULL,
          content_hash text NOT NULL,
          status text NOT NULL,
          scoring_key text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (simulation_id, version)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS simulation_definition_org_idx "
        f"ON {_SCHEMA}.simulation_definition (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.trust_tier (
          organization_id text NOT NULL,
          tier text NOT NULL,
          approved boolean NOT NULL DEFAULT false,
          max_quota jsonb NOT NULL,
          PRIMARY KEY (organization_id, tier)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS simulation_trust_tier_org_idx "
        f"ON {_SCHEMA}.trust_tier (organization_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.run_evidence (
          run_id text PRIMARY KEY,
          organization_id text NOT NULL,
          simulation_id text NOT NULL,
          definition_hash text NOT NULL,
          runtime_version text NOT NULL,
          inputs_hash text NOT NULL,
          entries jsonb NOT NULL,
          outcome text NOT NULL,
          head_hash text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS simulation_run_evidence_org_idx "
        f"ON {_SCHEMA}.run_evidence (organization_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS simulation_run_evidence_sim_idx "
        f"ON {_SCHEMA}.run_evidence (simulation_id)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.score (
          score_id text PRIMARY KEY,
          organization_id text NOT NULL,
          run_id text NOT NULL,
          profile_id text NOT NULL,
          profile_version text NOT NULL,
          seed text NOT NULL,
          value double precision NOT NULL,
          breakdown jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS simulation_score_org_idx ON {_SCHEMA}.score (organization_id)"
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS simulation_score_run_idx ON {_SCHEMA}.score (run_id)")

    connection = op.get_bind()
    for table in _TENANT_TABLES:
        apply_tenant_rls(connection, schema=_SCHEMA, table=table, tenant_column="organization_id")


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TENANT_TABLES):
        drop_tenant_rls(connection, schema=_SCHEMA, table=table)
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.score")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.run_evidence")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.trust_tier")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.simulation_definition")
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
