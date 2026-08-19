"""SQLAlchemy Core table for the assistant data owner (schema ``northstar_assistant``).

Mirrors migration ``000029_assistant``. The single ``assistant_setting`` row per tenant persists the
admin-selected active model so the choice survives restarts; ``organization_id`` is the RLS tenant
column and primary key.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Column, DateTime, MetaData, String, Table

ASSISTANT_SCHEMA = "northstar_assistant"


@dataclass(frozen=True)
class AssistantTables:
    setting: Table


def build_assistant_tables(
    metadata: MetaData, *, schema: str | None = ASSISTANT_SCHEMA
) -> AssistantTables:
    setting = Table(
        "assistant_setting",
        metadata,
        Column("organization_id", String, primary_key=True),
        Column("active_model", String, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    return AssistantTables(setting=setting)
