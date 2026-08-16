"""Messaging adapters: provider port + persistence, behind the application ports (rule 10)."""

from __future__ import annotations

from .provider import InMemoryMessageProvider
from .repositories import InMemoryMessagingRepository, SqlAlchemyMessagingRepository
from .tables import (
    MESSAGING_SCHEMA,
    MESSAGING_TENANT_TABLES,
    MessagingTables,
    build_messaging_tables,
)

__all__ = [
    "MESSAGING_SCHEMA",
    "MESSAGING_TENANT_TABLES",
    "InMemoryMessageProvider",
    "InMemoryMessagingRepository",
    "MessagingTables",
    "SqlAlchemyMessagingRepository",
    "build_messaging_tables",
]
