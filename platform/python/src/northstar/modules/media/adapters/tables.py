"""SQLAlchemy Core tables for the media data owner (schema ``northstar_media``).

Infrastructure is allowed here (rule 10). The table mirrors migration ``000018_media`` exactly and
lives in the ``northstar_media`` schema on PostgreSQL. The tenant-scoped table carries an explicit
``organization_id`` — the RLS tenant column (defense-in-depth, FR-POL-004) and the predicate every
repository query includes. The builder is parameterised on ``schema`` so portable tests can
materialise the same shape in another schema.
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

MEDIA_SCHEMA = "northstar_media"


def _jsonb() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class MediaTables:
    """Core table backing media assets and their accessible alternatives."""

    media_asset: Table


def build_media_tables(metadata: MetaData, *, schema: str | None = MEDIA_SCHEMA) -> MediaTables:
    """Define the media tables on ``metadata`` in ``schema`` (mirrors migration 000018)."""
    media_asset = Table(
        "media_asset",
        metadata,
        Column("asset_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("media_type", String, nullable=False),
        Column("content_type", String, nullable=False),
        Column("blob_ref", String, nullable=False),
        Column("byte_size", Integer, nullable=False),
        Column("title", String, nullable=True),
        Column("state", String, nullable=False),
        Column("transcript", _jsonb(), nullable=True),
        Column("captions", _jsonb(), nullable=False),
        Column("alt_text", String, nullable=True),
        Column("decorative", Boolean, nullable=False),
        Column("duration_seconds", Float, nullable=True),
        Column("created_by", _jsonb(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("policy_decision_id", String, nullable=True),
        schema=schema,
    )
    Index("media_asset_org_idx", media_asset.c.organization_id)
    Index("media_asset_state_idx", media_asset.c.organization_id, media_asset.c.state)

    return MediaTables(media_asset=media_asset)
