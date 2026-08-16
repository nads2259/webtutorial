"""SQLAlchemy implementation of the kernel Unit-of-Work port.

Honors the :class:`~northstar.kernel.persistence.ports.UnitOfWorkPort` contract (LSP,
rule 20): entering opens a session/transaction; commit is explicit; exiting without an
explicit commit (or because of a propagating exception) rolls back. Raw SQLAlchemy errors
raised on commit are translated to the typed :mod:`.errors` hierarchy at this boundary.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from northstar.kernel.persistence.ports import UnitOfWorkPort

from .errors import map_persistence_error


class SqlAlchemyUnitOfWork(UnitOfWorkPort):
    """A transactional boundary backed by a single SQLAlchemy :class:`Session`.

    Construct with a ``session_factory`` (see
    :func:`~northstar.adapters.persistence_sqlalchemy.engine.create_session_factory`). The
    session is created on ``__enter__`` and closed on ``__exit__``; a fresh instance is used
    per unit so units never share mutable state.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False

    @property
    def session(self) -> Session:
        """The active session; valid only inside the ``with`` block."""
        if self._session is None:
            raise RuntimeError("unit of work is not active; use it as a context manager")
        return self._session

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc is not None or not self._committed:
                self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    def commit(self) -> None:
        try:
            self.session.commit()
        except SQLAlchemyError as err:
            self.session.rollback()
            raise map_persistence_error(err) from err
        self._committed = True

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
        self._committed = False
