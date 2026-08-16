"""SQLAlchemy-backed identity directory and session store (schema ``northstar_identity``).

Infrastructure adapter (rule 10) implementing :class:`IdentityDirectoryPort` and
:class:`SessionStorePort` over the Core tables in :mod:`.tables`. Writes go through the kernel's
:class:`~northstar.adapters.persistence_sqlalchemy.unit_of_work.SqlAlchemyUnitOfWork` so they are
transactional; the session store persists only the token hash (docs/07 §4). Row access is fully
parameterised (rule 50) — no string interpolation of values.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..application.ports import IdentityDirectoryPort, SessionStorePort
from ..domain.model import (
    AssuranceLevel,
    ExternalIdentity,
    Session,
    Subject,
    SubjectType,
    User,
)
from .security import hash_session_token
from .tables import IdentityTables

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]


def _row_to_subject(row: object) -> Subject:
    return Subject(
        subject_id=row.subject_id,
        subject_type=SubjectType(row.subject_type),
        created_at=row.created_at,
        tenant_scope=row.tenant_scope,
    )


def _row_to_session(row: object) -> Session:
    return Session(
        session_id=row.session_id,
        subject_id=row.subject_id,
        created_at=row.created_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        assurance=AssuranceLevel(row.assurance),
        tenant_scope=row.tenant_scope,
        revoked_at=row.revoked_at,
        rotated_from=row.rotated_from,
    )


class SqlAlchemyIdentityDirectory(IdentityDirectoryPort):
    """Persists subjects/users and resolves/provisions them by external identity."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: IdentityTables,
        id_factory: IdFactory,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables
        self._id_factory = id_factory
        self._clock = clock

    def add_subject(self, subject: Subject) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._tables.subject).values(
                    subject_id=subject.subject_id,
                    subject_type=subject.subject_type.value,
                    created_at=subject.created_at,
                    tenant_scope=subject.tenant_scope,
                )
            )
            uow.commit()

    def find_by_external_identity(self, identity: ExternalIdentity) -> tuple[Subject, User] | None:
        ext = self._tables.external_identity
        users = self._tables.user_account
        subjects = self._tables.subject
        with self._session_factory() as session:
            row = session.execute(
                select(
                    subjects,
                    users.c.user_id,
                    users.c.primary_email,
                    users.c.display_name,
                )
                .select_from(
                    ext.join(users, ext.c.user_id == users.c.user_id).join(
                        subjects, users.c.subject_id == subjects.c.subject_id
                    )
                )
                .where(ext.c.issuer == identity.issuer, ext.c.subject == identity.subject)
            ).first()
        if row is None:
            return None
        subject = _row_to_subject(row)
        user = User(
            user_id=row.user_id,
            subject_id=row.subject_id,
            external_identities=(identity,),
            primary_email=row.primary_email,
            display_name=row.display_name,
        )
        return subject, user

    def provision(
        self,
        *,
        identity: ExternalIdentity,
        email: str | None,
        display_name: str | None,
        tenant_scope: str | None,
    ) -> tuple[Subject, User]:
        now = self._clock()
        subject = Subject(
            subject_id=self._id_factory(),
            subject_type=SubjectType.USER,
            created_at=now,
            tenant_scope=tenant_scope,
        )
        user = User(
            user_id=self._id_factory(),
            subject_id=subject.subject_id,
            external_identities=(identity,),
            primary_email=email,
            display_name=display_name,
        )
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._tables.subject).values(
                    subject_id=subject.subject_id,
                    subject_type=subject.subject_type.value,
                    created_at=subject.created_at,
                    tenant_scope=subject.tenant_scope,
                )
            )
            uow.session.execute(
                insert(self._tables.user_account).values(
                    user_id=user.user_id,
                    subject_id=user.subject_id,
                    primary_email=user.primary_email,
                    display_name=user.display_name,
                    created_at=now,
                )
            )
            uow.session.execute(
                insert(self._tables.external_identity).values(
                    issuer=identity.issuer,
                    subject=identity.subject,
                    user_id=user.user_id,
                    linked_at=now,
                )
            )
            uow.commit()
        return subject, user


class SqlAlchemySessionStore(SessionStorePort):
    """Persists sessions by token hash and materialises them back as domain value objects."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: IdentityTables,
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def create(self, *, raw_token: str, session: Session) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._tables.session).values(
                    session_id=session.session_id,
                    subject_id=session.subject_id,
                    token_sha256=hash_session_token(raw_token),
                    created_at=session.created_at,
                    idle_expires_at=session.idle_expires_at,
                    absolute_expires_at=session.absolute_expires_at,
                    assurance=session.assurance.value,
                    tenant_scope=session.tenant_scope,
                    revoked_at=session.revoked_at,
                    rotated_from=session.rotated_from,
                )
            )
            uow.commit()

    def authenticate(self, *, raw_token: str, now: datetime) -> Session | None:
        table = self._tables.session
        with self._session_factory() as session:
            row = session.execute(
                select(table).where(table.c.token_sha256 == hash_session_token(raw_token))
            ).first()
        if row is None:
            return None
        found = _row_to_session(row)
        return found if found.is_active(now) else None

    def get(self, session_id: str) -> Session | None:
        table = self._tables.session
        with self._session_factory() as session:
            row = session.execute(select(table).where(table.c.session_id == session_id)).first()
        return None if row is None else _row_to_session(row)

    def replace(self, session: Session) -> None:
        table = self._tables.session
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                update(table)
                .where(table.c.session_id == session.session_id)
                .values(
                    idle_expires_at=session.idle_expires_at,
                    absolute_expires_at=session.absolute_expires_at,
                    assurance=session.assurance.value,
                    revoked_at=session.revoked_at,
                    rotated_from=session.rotated_from,
                )
            )
            uow.commit()
