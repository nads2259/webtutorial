"""Typed persistence errors and a mapping from raw SQLAlchemy exceptions.

Adapters translate driver/ORM exceptions into a small, stable typed hierarchy so callers
never catch infrastructure-specific types (rule 20/30). This is deliberately narrow: it
distinguishes integrity violations (constraint breaches) from operational failures
(connectivity, transient driver errors) and falls back to a generic persistence error.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError


class PersistenceError(Exception):
    """Base class for adapter-level persistence failures."""


class IntegrityViolation(PersistenceError):  # noqa: N818 canonical error name
    """A constraint (unique/foreign-key/not-null/check) was violated."""


class OperationalFailure(PersistenceError):  # noqa: N818 canonical error name
    """A connectivity/transient driver failure occurred."""


def map_persistence_error(exc: SQLAlchemyError) -> PersistenceError:
    """Map a raw SQLAlchemy exception to a typed :class:`PersistenceError`.

    The original exception is preserved as ``__cause__`` for diagnostics without leaking the
    infrastructure type across the boundary.
    """
    if isinstance(exc, IntegrityError):
        mapped: PersistenceError = IntegrityViolation(str(exc.orig or exc))
    elif isinstance(exc, OperationalError):
        mapped = OperationalFailure(str(exc.orig or exc))
    else:
        mapped = PersistenceError(str(exc))
    mapped.__cause__ = exc
    return mapped
