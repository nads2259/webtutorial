"""Support HTTP inbound adapter (thin, over the kernel command/query buses)."""

from .router import (
    SupportApiDependencies,
    bind_support_dependencies,
    create_support_router,
)

__all__ = [
    "SupportApiDependencies",
    "bind_support_dependencies",
    "create_support_router",
]
