"""SQLAlchemy Core tables for the codelab data owner (schema ``northstar_codelab``).

Mirrors migration ``000027_codelab``. The single ``code_run`` table is an append-only, tenant-scoped
log of every tracked execution; ``organization_id`` is the RLS tenant column and the predicate every
query includes (defense-in-depth, FR-POL-004).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

CODELAB_SCHEMA = "northstar_codelab"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class CodelabTables:
    code_run: Table


def build_codelab_tables(
    metadata: MetaData, *, schema: str | None = CODELAB_SCHEMA
) -> CodelabTables:
    code_run = Table(
        "code_run",
        metadata,
        Column("run_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("actor_id", String, nullable=False),
        Column("language", String, nullable=False),
        Column("code", Text, nullable=False),
        Column("lesson_id", String, nullable=True),
        Column("block_id", String, nullable=True),
        Column("stdout", Text, nullable=False),
        Column("stderr", Text, nullable=False),
        Column("exit_code", Integer, nullable=False),
        Column("duration_ms", Integer, nullable=False),
        Column("timed_out", Boolean, nullable=False),
        Column("truncated", Boolean, nullable=False),
        Column("outcome", String, nullable=False),
        Column("record_sha256", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("codelab_code_run_org_idx", code_run.c.organization_id)
    Index("codelab_code_run_actor_idx", code_run.c.organization_id, code_run.c.actor_id)
    Index("codelab_code_run_created_idx", code_run.c.created_at)
    return CodelabTables(code_run=code_run)
