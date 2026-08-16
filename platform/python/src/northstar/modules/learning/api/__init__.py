"""Learning HTTP inbound adapter (thin, over the kernel command/query buses)."""

from .router import (
    LearningApiDependencies,
    bind_learning_dependencies,
    create_learning_router,
)

__all__ = [
    "LearningApiDependencies",
    "bind_learning_dependencies",
    "create_learning_router",
]
