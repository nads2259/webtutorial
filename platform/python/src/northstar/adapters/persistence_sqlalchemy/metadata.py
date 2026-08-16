"""Shared declarative base and metadata for the SQLAlchemy adapter.

A single :class:`Base` with a deterministic naming convention keeps constraint/index names
stable and diffable across environments and migrations (rule 80). The framework-registry
tables live in the dedicated ``northstar_meta`` schema on PostgreSQL; portable unit tests
against SQLite create objects in the default schema.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

METADATA_SCHEMA = "northstar_meta"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all adapter-owned ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
