"""Support adapters: tenant-scoped persistence behind the application ports (rule 10)."""

from __future__ import annotations

from .repositories import InMemorySupportRepository, SqlAlchemySupportRepository
from .tables import (
    SUPPORT_SCHEMA,
    SUPPORT_TENANT_TABLES,
    SupportTables,
    build_support_tables,
)

__all__ = [
    "SUPPORT_SCHEMA",
    "SUPPORT_TENANT_TABLES",
    "InMemorySupportRepository",
    "SqlAlchemySupportRepository",
    "SupportTables",
    "build_support_tables",
]
