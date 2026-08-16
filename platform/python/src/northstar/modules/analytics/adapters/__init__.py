"""Analytics adapters: persistence + optional GA4 import, behind the application ports (rule 10)."""

from __future__ import annotations

from .ga4 import InMemoryGa4Adapter
from .repositories import InMemoryAnalyticsRepository, SqlAlchemyAnalyticsRepository
from .tables import (
    ANALYTICS_SCHEMA,
    ANALYTICS_TENANT_TABLES,
    AnalyticsTables,
    build_analytics_tables,
)

__all__ = [
    "ANALYTICS_SCHEMA",
    "ANALYTICS_TENANT_TABLES",
    "AnalyticsTables",
    "InMemoryAnalyticsRepository",
    "InMemoryGa4Adapter",
    "SqlAlchemyAnalyticsRepository",
    "build_analytics_tables",
]
