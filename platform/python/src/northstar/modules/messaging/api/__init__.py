"""Messaging HTTP inbound adapter (thin, over the kernel command bus)."""

from .router import (
    MessagingApiDependencies,
    bind_messaging_dependencies,
    create_messaging_router,
)

__all__ = [
    "MessagingApiDependencies",
    "bind_messaging_dependencies",
    "create_messaging_router",
]
