"""Alembic environment entry point.

Runs migrations against the URL supplied on the :class:`alembic.config.Config` (see
:func:`northstar.adapters.persistence_sqlalchemy.migrations.config.make_alembic_config`) or,
as a fallback, ``DATABASE_URL``. Uses a fresh non-pooling engine so migration tooling never
holds connections open. An optional ``version_table_schema`` main option lets callers isolate
the Alembic bookkeeping table (used by integration tests running in a throwaway schema).
"""

from __future__ import annotations

from alembic import context

from northstar.adapters.persistence_sqlalchemy.engine import (
    create_engine_from_url,
    resolve_database_url,
)
from northstar.adapters.persistence_sqlalchemy.metadata import Base

config = context.config
target_metadata = Base.metadata


def _resolve_url() -> str:
    return resolve_database_url(config.get_main_option("sqlalchemy.url"))


def _version_table_schema() -> str | None:
    return config.get_main_option("version_table_schema")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=_version_table_schema(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine_from_url(_resolve_url())
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table_schema=_version_table_schema(),
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
