"""SQLAlchemy Core tables for the AI data owner (schema ``northstar_ai``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000009_ai`` exactly:

* ``prompt_package`` — the IMMUTABLE versioned prompt registry (global config, no tenant column);
  a ``(package_id, version)`` is written once and never updated (FR-AI-002);
* ``ai_memory`` — purpose-limited, deletable per-owner memory, tenant-scoped (FR-AI-006);
* ``ai_trace`` — per-interaction provenance (model/provider/prompt/tools/cost), tenant-scoped
  (FR-AI-009).

Tenant-scoped tables carry an ``organization_id`` column and receive FORCE ROW LEVEL SECURITY as
defense-in-depth (rule 50). The builder is parameterised on ``schema`` so tests can materialise the
same shape in a throwaway schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

AI_SCHEMA = "northstar_ai"

# Tenant-scoped tables that receive FORCE ROW LEVEL SECURITY (prompt_package is global config).
AI_TENANT_TABLES: tuple[str, ...] = ("ai_memory", "ai_trace", "ai_budget", "ai_cost_ledger")


@dataclass(frozen=True)
class AiTables:
    """The AI module tables plus the schema they live in."""

    schema: str
    prompt_package: Table
    ai_memory: Table
    ai_trace: Table
    ai_budget: Table
    ai_cost_ledger: Table


def build_ai_tables(metadata: MetaData, *, schema: str | None = AI_SCHEMA) -> AiTables:
    """Define the AI tables on ``metadata`` in ``schema`` (mirrors migration 000009)."""
    prompt_package = Table(
        "prompt_package",
        metadata,
        Column("package_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("actor_profile", String, nullable=False),
        Column("purpose", Text, nullable=False),
        Column("system_instruction", Text, nullable=False),
        Column("developer_instructions", JSONB, nullable=False),
        Column("declared_tools", JSONB, nullable=False),
        Column("retrieval_profile", String, nullable=True),
        Column("memory_policy", String, nullable=False),
        Column("evaluation_suite", String, nullable=False),
        Column("status", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    ai_memory = Table(
        "ai_memory",
        metadata,
        Column("memory_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("owner_id", String, nullable=False),
        Column("memory_class", String, nullable=False),
        Column("purpose", Text, nullable=False),
        Column("classification", String, nullable=False),
        Column("content", Text, nullable=False),
        Column("retention", String, nullable=False),
        Column("inferred", Boolean, nullable=False, server_default="false"),
        Column("supersedes", String, nullable=True),
        Column("superseded_by", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    ai_trace = Table(
        "ai_trace",
        metadata,
        Column("trace_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("actor_id", String, nullable=False),
        Column("actor_profile", String, nullable=False),
        Column("provider", String, nullable=False),
        Column("model", String, nullable=False),
        Column("prompt_package", String, nullable=False),
        Column("input_tokens", Integer, nullable=False),
        Column("output_tokens", Integer, nullable=False),
        Column("cost_units", Float, nullable=False),
        Column("tool_calls", JSONB, nullable=False),
        Column("citations_valid", Integer, nullable=False),
        Column("citations_rejected", Integer, nullable=False),
        Column("refused", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    ai_budget = Table(
        "ai_budget",
        metadata,
        Column("budget_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("scope", String, nullable=False),
        Column("scope_id", String, nullable=False),
        Column("limit_units", Float, nullable=False),
        Column("budget_window", String, nullable=False, server_default="monthly"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    ai_cost_ledger = Table(
        "ai_cost_ledger",
        metadata,
        Column("entry_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("actor_id", String, nullable=False),
        Column("workflow_id", String, nullable=True),
        Column("cost_units", Float, nullable=False),
        Column("provider_cost", Float, nullable=False),
        Column("provider", String, nullable=False),
        Column("correlation_id", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    return AiTables(
        schema=schema or AI_SCHEMA,
        prompt_package=prompt_package,
        ai_memory=ai_memory,
        ai_trace=ai_trace,
        ai_budget=ai_budget,
        ai_cost_ledger=ai_cost_ledger,
    )
