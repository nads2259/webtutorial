"""SQLAlchemy Core tables for the simulation data owner (schema ``northstar_simulation``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000011_simulation`` exactly and
live in the ``northstar_simulation`` schema. Every table is tenant-scoped by an explicit
``organization_id`` column — the RLS tenant column (defense-in-depth, rule 50/FR-SIM-005) and the
predicate every repository query includes. The ``scoring_key`` column holds the hidden anti-cheat
secret and is NEVER projected into the definition document, the sandbox or the AI coach's context.
The builder is parameterised on ``schema`` so portable tests can materialise the same shape in a
throwaway schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

SIMULATION_SCHEMA = "northstar_simulation"

# Every simulation table is tenant-scoped and receives FORCE ROW LEVEL SECURITY (rule 50).
SIMULATION_TENANT_TABLES: tuple[str, ...] = (
    "simulation_definition",
    "trust_tier",
    "run_evidence",
    "score",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class SimulationTables:
    """The simulation module tables plus the schema they live in."""

    schema: str
    simulation_definition: Table
    trust_tier: Table
    run_evidence: Table
    score: Table


def build_simulation_tables(
    metadata: MetaData, *, schema: str | None = SIMULATION_SCHEMA
) -> SimulationTables:
    """Define the simulation tables on ``metadata`` in ``schema`` (mirrors migration 000011)."""
    simulation_definition = Table(
        "simulation_definition",
        metadata,
        Column("simulation_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document", _jsonb(), nullable=False),
        Column("content_hash", String, nullable=False),
        Column("status", String, nullable=False),
        Column("scoring_key", Text, nullable=False, server_default=""),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("simulation_definition_org_idx", simulation_definition.c.organization_id)

    trust_tier = Table(
        "trust_tier",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("tier", String, primary_key=True),
        Column("approved", Boolean, nullable=False, server_default="false"),
        Column("max_quota", _jsonb(), nullable=False),
        schema=schema,
    )
    Index("simulation_trust_tier_org_idx", trust_tier.c.organization_id)

    run_evidence = Table(
        "run_evidence",
        metadata,
        Column("run_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("simulation_id", String, nullable=False),
        Column("definition_hash", String, nullable=False),
        Column("runtime_version", String, nullable=False),
        Column("inputs_hash", String, nullable=False),
        Column("entries", _jsonb(), nullable=False),
        Column("outcome", String, nullable=False),
        Column("head_hash", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("simulation_run_evidence_org_idx", run_evidence.c.organization_id)
    Index("simulation_run_evidence_sim_idx", run_evidence.c.simulation_id)

    score = Table(
        "score",
        metadata,
        Column("score_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("run_id", String, nullable=False),
        Column("profile_id", String, nullable=False),
        Column("profile_version", String, nullable=False),
        Column("seed", String, nullable=False),
        Column("value", Float, nullable=False),
        Column("breakdown", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("simulation_score_org_idx", score.c.organization_id)
    Index("simulation_score_run_idx", score.c.run_id)

    return SimulationTables(
        schema=schema or SIMULATION_SCHEMA,
        simulation_definition=simulation_definition,
        trust_tier=trust_tier,
        run_evidence=run_evidence,
        score=score,
    )
