"""Programmatic Alembic :class:`Config` builder (DRY, rule 21).

Tests and the (future) CLI construct the migration environment the same way, so behavior can
never drift between them. ``script_location`` points at this package directory (which holds
``env.py`` and ``versions/``); the URL is injected explicitly or resolved from the
environment, never hard-coded.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config

from northstar.adapters.persistence_sqlalchemy.engine import resolve_database_url

_MIGRATIONS_DIR = Path(__file__).resolve().parent


def make_alembic_config(url: str | None = None) -> Config:
    """Build an Alembic :class:`Config` for this adapter's migrations.

    ``url`` overrides the resolved ``DATABASE_URL``; useful for tests targeting a throwaway
    schema or database.
    """
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", resolve_database_url(url))
    return config
