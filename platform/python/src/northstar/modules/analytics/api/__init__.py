"""Analytics HTTP inbound adapter (thin, over the kernel command/query buses)."""

from .router import (
    AnalyticsApiDependencies,
    bind_analytics_dependencies,
    create_analytics_router,
)

__all__ = [
    "AnalyticsApiDependencies",
    "bind_analytics_dependencies",
    "create_analytics_router",
]
