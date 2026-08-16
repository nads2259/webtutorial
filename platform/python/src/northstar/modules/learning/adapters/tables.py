"""SQLAlchemy Core tables for the learning data owner (schema ``northstar_learning``).

Infrastructure is allowed here (rule 10). Tables mirror migration ``000016`` exactly and live in the
``northstar_learning`` schema. Every table is tenant-scoped by an explicit ``organization_id``
column — the RLS tenant column (defense-in-depth, rule 50) and the predicate every repository query
includes. The builder is parameterised on ``schema`` so portable tests can materialise the same
shape in a throwaway schema.

Progress lives in ``progress`` (its OWN table), NOT derived from analytics events (FR-LRN-002).
Anonymous, device/session-scoped progress lives in ``anonymous_progress`` (migration 000025) and is
merged into the authenticated ``progress`` on sign-in (UX-010). The
``assessment_item.sealed`` flag records that an item version has scored an attempt and is therefore
immutable (FR-LRN-004). ``profile_feature`` carries the inferred-profile inventory a learner can
inspect/correct/reset (EVAL-PRIV-004).
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
)
from sqlalchemy.dialects.postgresql import JSONB

LEARNING_SCHEMA = "northstar_learning"

LEARNING_TENANT_TABLES: tuple[str, ...] = (
    "domain",
    "learning_path",
    "course",
    "progress",
    "anonymous_progress",
    "overlay",
    "assessment_item",
    "attempt",
    "completion_rule",
    "credential",
    "profile_feature",
)


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class LearningTables:
    """The learning module tables plus the schema they live in."""

    schema: str
    domain: Table
    learning_path: Table
    course: Table
    progress: Table
    anonymous_progress: Table
    overlay: Table
    assessment_item: Table
    attempt: Table
    completion_rule: Table
    credential: Table
    profile_feature: Table


def build_learning_tables(
    metadata: MetaData, *, schema: str | None = LEARNING_SCHEMA
) -> LearningTables:
    """Define the learning tables on ``metadata`` in ``schema`` (mirrors migration 000016)."""
    domain = Table(
        "domain",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("domain_id", String, primary_key=True),
        Column("title", String, nullable=False),
        Column("slug", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_domain_org_idx", domain.c.organization_id)

    learning_path = Table(
        "learning_path",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("path_id", String, primary_key=True),
        Column("domain_id", String, nullable=False),
        Column("title", String, nullable=False),
        Column("course_ids", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_path_org_idx", learning_path.c.organization_id)

    course = Table(
        "course",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("course_id", String, primary_key=True),
        Column("domain_id", String, nullable=False),
        Column("path_id", String, nullable=True),
        Column("title", String, nullable=False),
        Column("sections", _jsonb(), nullable=False),
        Column("published", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_course_org_idx", course.c.organization_id)

    progress = Table(
        "progress",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("subject_id", String, primary_key=True),
        Column("course_id", String, primary_key=True),
        Column("resume", _jsonb(), nullable=False),
        Column("modality", String, nullable=False),
        Column("completed_sections", _jsonb(), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("learning_progress_org_idx", progress.c.organization_id)
    Index("learning_progress_subject_idx", progress.c.organization_id, progress.c.subject_id)

    anonymous_progress = Table(
        "anonymous_progress",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("anonymous_id", String, primary_key=True),
        Column("course_id", String, primary_key=True),
        Column("resume", _jsonb(), nullable=False),
        Column("modality", String, nullable=False),
        Column("completed_sections", _jsonb(), nullable=False),
        Column("claimed_by", String, nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("learning_anon_progress_org_idx", anonymous_progress.c.organization_id)
    Index(
        "learning_anon_progress_device_idx",
        anonymous_progress.c.organization_id,
        anonymous_progress.c.anonymous_id,
    )

    overlay = Table(
        "overlay",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("overlay_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("course_id", String, nullable=False),
        Column("section_id", String, nullable=False),
        Column("block_id", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("body", String, nullable=False),
        Column("quote", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_overlay_org_idx", overlay.c.organization_id)
    Index(
        "learning_overlay_owner_idx",
        overlay.c.organization_id,
        overlay.c.subject_id,
        overlay.c.course_id,
    )

    assessment_item = Table(
        "assessment_item",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("item_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("kind", String, nullable=False),
        Column("prompt", String, nullable=False),
        Column("answer_key", _jsonb(), nullable=False),
        Column("choices", _jsonb(), nullable=False),
        Column("points", Integer, nullable=False),
        Column("pass_ratio", Float, nullable=False),
        Column("max_attempts", Integer, nullable=False),
        Column("accommodations", _jsonb(), nullable=False),
        Column("content_hash", String, nullable=False),
        Column("sealed", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_item_org_idx", assessment_item.c.organization_id)

    attempt = Table(
        "attempt",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("attempt_id", String, primary_key=True),
        Column("item_id", String, nullable=False),
        Column("item_version", String, nullable=False),
        Column("subject_id", String, nullable=False),
        Column("responses", _jsonb(), nullable=False),
        Column("raw", Integer, nullable=False),
        Column("max", Integer, nullable=False),
        Column("passed", Boolean, nullable=False),
        Column("feedback", String, nullable=False),
        Column("accommodations", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_attempt_org_idx", attempt.c.organization_id)
    Index(
        "learning_attempt_subject_idx",
        attempt.c.organization_id,
        attempt.c.subject_id,
        attempt.c.item_id,
    )

    completion_rule = Table(
        "completion_rule",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("rule_id", String, primary_key=True),
        Column("course_id", String, nullable=False),
        Column("required_section_ids", _jsonb(), nullable=False),
        Column("required_item_ids", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("learning_rule_org_idx", completion_rule.c.organization_id)

    credential = Table(
        "credential",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("credential_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("course_id", String, nullable=False),
        Column("rule_id", String, nullable=False),
        Column("evidence", _jsonb(), nullable=False),
        Column("verification_hash", String, nullable=False),
        Column("issued_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("learning_credential_org_idx", credential.c.organization_id)
    Index(
        "learning_credential_subject_idx",
        credential.c.organization_id,
        credential.c.subject_id,
        credential.c.course_id,
    )

    profile_feature = Table(
        "profile_feature",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("subject_id", String, primary_key=True),
        Column("name", String, primary_key=True),
        Column("value", String, nullable=False),
        Column("inferred", Boolean, nullable=False),
        Column("source", String, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("learning_profile_org_idx", profile_feature.c.organization_id)

    return LearningTables(
        schema=schema or LEARNING_SCHEMA,
        domain=domain,
        learning_path=learning_path,
        course=course,
        progress=progress,
        anonymous_progress=anonymous_progress,
        overlay=overlay,
        assessment_item=assessment_item,
        attempt=attempt,
        completion_rule=completion_rule,
        credential=credential,
        profile_feature=profile_feature,
    )
