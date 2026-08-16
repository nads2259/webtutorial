"""SQLAlchemy Core tables for the organization data owner (schema ``northstar_organization``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000004_org_entitlement``
exactly and live in the ``northstar_organization`` schema on PostgreSQL. Every tenant-scoped table
carries an explicit ``organization_id`` — the RLS tenant column enforced as defense-in-depth
(FR-POL-004) and the predicate every repository query includes. The builder is parameterised on
``schema`` so portable tests can materialise the same shape in another schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

ORGANIZATION_SCHEMA = "northstar_organization"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class OrganizationTables:
    """Core tables backing organizations, workspaces, teams and memberships."""

    organization: Table
    workspace: Table
    team: Table
    membership: Table


def build_organization_tables(
    metadata: MetaData, *, schema: str | None = ORGANIZATION_SCHEMA
) -> OrganizationTables:
    """Define the organization tables on ``metadata`` in ``schema`` (mirrors migration 000004)."""
    organization = Table(
        "organization",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    workspace = Table(
        "workspace",
        metadata,
        Column("workspace_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("workspace_org_idx", workspace.c.organization_id)

    team = Table(
        "team",
        metadata,
        Column("team_id", String, primary_key=True),
        Column("workspace_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("team_org_idx", team.c.organization_id)

    membership = Table(
        "membership",
        metadata,
        Column("membership_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("roles", _jsonb(), nullable=False),
        Column("workspace_id", String, nullable=True),
        Column("team_id", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("membership_org_idx", membership.c.organization_id)
    Index("membership_subject_idx", membership.c.subject_id)

    return OrganizationTables(
        organization=organization, workspace=workspace, team=team, membership=membership
    )
