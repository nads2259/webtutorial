"""SQLAlchemy Core tables for the research data owner (schema ``northstar_research``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000010_research`` exactly and
live in the ``northstar_research`` schema. Every table is tenant-scoped by an explicit
``organization_id`` column — the RLS tenant column (defense-in-depth, rule 50/FR-RSH-001) and the
predicate every repository query includes. The builder is parameterised on ``schema`` so portable
tests can materialise the same shape in a throwaway schema.
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
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

RESEARCH_SCHEMA = "northstar_research"

# Every research table is tenant-scoped and receives FORCE ROW LEVEL SECURITY (rule 50).
RESEARCH_TENANT_TABLES: tuple[str, ...] = (
    "workspace",
    "research_project",
    "research_document",
    "research_revision",
    "evidence_record",
    "claim",
    "dataset_ref",
    "experiment_ref",
    "research_question",
    "hypothesis",
    "research_method",
    "project_membership",
    "document_block",
    "document_simulation_link",
    "document_review",
    "review_event",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class ResearchTables:
    """The research module tables plus the schema they live in."""

    schema: str
    workspace: Table
    research_project: Table
    research_document: Table
    research_revision: Table
    evidence_record: Table
    claim: Table
    dataset_ref: Table
    experiment_ref: Table
    research_question: Table
    hypothesis: Table
    research_method: Table
    project_membership: Table
    document_block: Table
    document_simulation_link: Table
    document_review: Table
    review_event: Table


def build_research_tables(
    metadata: MetaData, *, schema: str | None = RESEARCH_SCHEMA
) -> ResearchTables:
    """Define the research tables on ``metadata`` in ``schema`` (mirrors migration 000010)."""
    workspace = Table(
        "workspace",
        metadata,
        Column("workspace_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_workspace_org_idx", workspace.c.organization_id)

    research_project = Table(
        "research_project",
        metadata,
        Column("project_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("workspace_id", String, nullable=False),
        Column("title", String, nullable=False),
        Column("research_question", Text, nullable=True),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_project_org_idx", research_project.c.organization_id)
    Index("research_project_workspace_idx", research_project.c.workspace_id)

    research_document = Table(
        "research_document",
        metadata,
        Column("document_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("title", String, nullable=False),
        Column("status", String, nullable=False),
        Column("content_tree", _jsonb(), nullable=False),
        Column("latest_revision_id", String, nullable=True),
        schema=schema,
    )
    Index("research_document_org_idx", research_document.c.organization_id)
    Index("research_document_project_idx", research_document.c.project_id)

    research_revision = Table(
        "research_revision",
        metadata,
        Column("revision_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_id", String, nullable=False),
        Column("parent_revision_id", String, nullable=True),
        Column("title", String, nullable=False),
        Column("status", String, nullable=False),
        Column("content_tree", _jsonb(), nullable=False),
        Column("content_hash", String, nullable=False),
        Column("created_by", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_revision_org_idx", research_revision.c.organization_id)
    Index("research_revision_document_idx", research_revision.c.document_id)

    evidence_record = Table(
        "evidence_record",
        metadata,
        Column("evidence_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_id", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("excerpt", Text, nullable=False),
        Column("version_hash", String, nullable=False),
        Column("object_id", String, nullable=True),
        Column("revision_id", String, nullable=True),
        Column("block_id", String, nullable=True),
        Column("chunk_id", String, nullable=True),
        Column("source_uri", String, nullable=True),
        Column("verified", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_evidence_org_idx", evidence_record.c.organization_id)
    Index("research_evidence_document_idx", evidence_record.c.document_id)

    claim = Table(
        "claim",
        metadata,
        Column("claim_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_id", String, nullable=False),
        Column("statement", Text, nullable=False),
        Column("evidence_ids", _jsonb(), nullable=False),
        Column("confidence", Float, nullable=True),
        Column("generated", Boolean, nullable=False, server_default="false"),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_claim_org_idx", claim.c.organization_id)
    Index("research_claim_document_idx", claim.c.document_id)

    dataset_ref = Table(
        "dataset_ref",
        metadata,
        Column("dataset_ref_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("owner_id", String, nullable=False),
        Column("version", String, nullable=False),
        Column("integrity_hash", String, nullable=False),
        Column("license", String, nullable=False),
        Column("classification", String, nullable=False),
        Column("retention", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_dataset_org_idx", dataset_ref.c.organization_id)
    Index("research_dataset_project_idx", dataset_ref.c.project_id)

    experiment_ref = Table(
        "experiment_ref",
        metadata,
        Column("experiment_ref_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("owner_id", String, nullable=False),
        Column("version", String, nullable=False),
        Column("reproducibility", String, nullable=False),
        Column("dataset_ref_ids", _jsonb(), nullable=False),
        Column("environment_digest", String, nullable=True),
        Column("seed", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_experiment_org_idx", experiment_ref.c.organization_id)
    Index("research_experiment_project_idx", experiment_ref.c.project_id)

    research_question = Table(
        "research_question",
        metadata,
        Column("question_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("prompt", Text, nullable=False),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_question_org_idx", research_question.c.organization_id)
    Index("research_question_project_idx", research_question.c.project_id)

    hypothesis = Table(
        "hypothesis",
        metadata,
        Column("hypothesis_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("question_id", String, nullable=False),
        Column("statement", Text, nullable=False),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_hypothesis_org_idx", hypothesis.c.organization_id)
    Index("research_hypothesis_project_idx", hypothesis.c.project_id)
    Index("research_hypothesis_question_idx", hypothesis.c.question_id)

    research_method = Table(
        "research_method",
        metadata,
        Column("method_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("project_id", String, nullable=False),
        Column("name", String, nullable=False),
        Column("description", Text, nullable=False, server_default=""),
        Column("created_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_method_org_idx", research_method.c.organization_id)
    Index("research_method_project_idx", research_method.c.project_id)

    project_membership = Table(
        "project_membership",
        metadata,
        Column("organization_id", String, nullable=False),
        Column("project_id", String, primary_key=True),
        Column("subject_id", String, primary_key=True),
        Column("role", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_membership_org_idx", project_membership.c.organization_id)
    Index("research_membership_project_idx", project_membership.c.project_id)

    document_block = Table(
        "document_block",
        metadata,
        Column("block_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_id", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("position", Integer, nullable=False, server_default="0"),
        Column("payload", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_docblock_org_idx", document_block.c.organization_id)
    Index("research_docblock_document_idx", document_block.c.document_id)

    document_simulation_link = Table(
        "document_simulation_link",
        metadata,
        Column("document_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("simulation_id", String, nullable=False),
        Column("version", String, nullable=False),
        Column("content_hash", String, nullable=False),
        Column("linked_by", String, nullable=False),
        Column("linked_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_simlink_org_idx", document_simulation_link.c.organization_id)

    document_review = Table(
        "document_review",
        metadata,
        Column("review_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("document_id", String, nullable=False, unique=True),
        Column("status", String, nullable=False),
        Column("authors", _jsonb(), nullable=False),
        Column("reviewers", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_review_org_idx", document_review.c.organization_id)
    Index("research_review_document_idx", document_review.c.document_id)

    review_event = Table(
        "review_event",
        metadata,
        Column("event_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("review_id", String, nullable=False),
        Column("from_status", String, nullable=False),
        Column("to_status", String, nullable=False),
        Column("action", String, nullable=False),
        Column("actor", String, nullable=False),
        Column("note", Text, nullable=True),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("research_review_event_org_idx", review_event.c.organization_id)
    Index("research_review_event_review_idx", review_event.c.review_id)

    return ResearchTables(
        schema=schema or RESEARCH_SCHEMA,
        workspace=workspace,
        research_project=research_project,
        research_document=research_document,
        research_revision=research_revision,
        evidence_record=evidence_record,
        claim=claim,
        dataset_ref=dataset_ref,
        experiment_ref=experiment_ref,
        research_question=research_question,
        hypothesis=hypothesis,
        research_method=research_method,
        project_membership=project_membership,
        document_block=document_block,
        document_simulation_link=document_simulation_link,
        document_review=document_review,
        review_event=review_event,
    )
