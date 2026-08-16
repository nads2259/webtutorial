"""Alembic migration environment for the SQLAlchemy persistence adapter.

Migrations are products (LAW-16, rule 80): versioned, reversible, each with a manifest that
validates against ``spec/contracts/schemas/migration-manifest.schema.json``. Baseline 000001
establishes the ``northstar_meta`` framework registry and enables pgvector.
"""

from __future__ import annotations

from .config import make_alembic_config

__all__ = ["make_alembic_config"]
