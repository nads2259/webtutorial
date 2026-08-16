"""Extension HTTP inbound adapter (thin, over the kernel buses)."""

from .router import (
    ExtensionApiDependencies,
    bind_extension_dependencies,
    create_extension_router,
)

__all__ = [
    "ExtensionApiDependencies",
    "bind_extension_dependencies",
    "create_extension_router",
]
