"""Commerce HTTP inbound adapter (thin, over the kernel command bus)."""

from .router import (
    CommerceApiDependencies,
    bind_commerce_dependencies,
    create_commerce_router,
)

__all__ = [
    "CommerceApiDependencies",
    "bind_commerce_dependencies",
    "create_commerce_router",
]
