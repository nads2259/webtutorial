"""AI adapters: prompt/memory/trace repositories, retrieval gateway and tool executor (rule 10)."""

from __future__ import annotations

from .repositories import (
    InMemoryMemoryRepository,
    InMemoryPromptRegistry,
    InMemoryTraceRepository,
    SqlAlchemyMemoryRepository,
    SqlAlchemyPromptRegistry,
    SqlAlchemyTraceRepository,
)
from .retrieval_gateway import BusRetrievalGateway, InMemoryRetrievalGateway
from .tables import AI_SCHEMA, AI_TENANT_TABLES, AiTables, build_ai_tables
from .tool_executor import CallableToolExecutor

__all__ = [
    "AI_SCHEMA",
    "AI_TENANT_TABLES",
    "AiTables",
    "BusRetrievalGateway",
    "CallableToolExecutor",
    "InMemoryMemoryRepository",
    "InMemoryPromptRegistry",
    "InMemoryRetrievalGateway",
    "InMemoryTraceRepository",
    "SqlAlchemyMemoryRepository",
    "SqlAlchemyPromptRegistry",
    "SqlAlchemyTraceRepository",
    "build_ai_tables",
]
