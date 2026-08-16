"""Kernel persistence ports (pure abstractions, no infrastructure).

The kernel declares *how* the application talks to durable storage — the Unit-of-Work and
Repository ports — without knowing *which* engine backs them. Concrete SQLAlchemy adapters
implement these ports behind the boundary (LAW-12); no infra import lives here (rule 10).
"""

from __future__ import annotations

from .ports import RepositoryPort, UnitOfWorkPort

__all__ = ["RepositoryPort", "UnitOfWorkPort"]
