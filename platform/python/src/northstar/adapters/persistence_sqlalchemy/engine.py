"""Engine and session factory construction (reads ``DATABASE_URL``).

Configuration is injected at the edge (rule 20/50): the URL comes from the environment or an
explicit argument, never hard-coded, and secrets are never logged. ``NullPool`` is used so
short-lived tools (migrations, tests) do not leave connections open.
"""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL_ENV = "DATABASE_URL"


class DatabaseUrlMissing(RuntimeError):  # noqa: N818 canonical error name
    """``DATABASE_URL`` was required but is not set in the environment."""

    def __init__(self, env_var: str = DATABASE_URL_ENV) -> None:
        super().__init__(f"required environment variable '{env_var}' is not set")
        self.env_var = env_var


def resolve_database_url(explicit: str | None = None, *, env_var: str = DATABASE_URL_ENV) -> str:
    """Return the database URL from ``explicit`` or the environment.

    Raises :class:`DatabaseUrlMissing` (deny-by-default) when neither source provides one.
    """
    url = explicit if explicit is not None else os.environ.get(env_var)
    if not url:
        raise DatabaseUrlMissing(env_var)
    return url


def create_engine_from_url(url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.x :class:`Engine` for ``url`` using a non-pooling strategy."""
    from sqlalchemy import pool

    return create_engine(url, echo=echo, future=True, poolclass=pool.NullPool)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a :class:`sessionmaker` bound to ``engine`` (explicit commits, no autoflush)."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
