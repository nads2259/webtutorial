"""Adapters for local (email + password) auth (schema ``northstar_identity``).

Infrastructure (rule 10): a stdlib-``scrypt`` password hasher (salted, memory-hard, dependency-free —
swappable for argon2/bcrypt behind :class:`PasswordHasherPort`) and SQLAlchemy stores for accounts,
verification tokens and account events. Every read/write is tenant-scoped by ``organization_id`` and
sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth (rule 50).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Table, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.local import (
    AccountEvent,
    AccountEventType,
    LocalAuthError,
    PasswordCredential,
    VerificationPurpose,
    VerificationToken,
)
from ..domain.model import SubjectType
from .tables import IdentityTables

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]

# scrypt cost parameters (RFC 7914). n=2**14 is a sensible interactive default.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


class ScryptPasswordHasher:
    """Salted scrypt password hasher. Encoded form: ``scrypt$n$r$p$salt_hex$hash_hex``."""

    def hash(self, password: str) -> str:
        salt = os.urandom(16)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            dklen=_SCRYPT_DKLEN, maxmem=0,
        )
        return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"

    def verify(self, *, password: str, encoded: str) -> bool:
        try:
            scheme, n_s, r_s, p_s, salt_hex, hash_hex = encoded.split("$")
            if scheme != "scrypt":
                return False
            derived = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n_s), r=int(r_s), p=int(p_s),
                dklen=len(bytes.fromhex(hash_hex)), maxmem=0,
            )
            return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
        except (ValueError, TypeError):
            return False


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyLocalAccountStore:
    """Creates/reads local password accounts (subject + user_account + password_credential)."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: IdentityTables,
        id_factory: IdFactory,
        clock: Clock,
    ) -> None:
        self._sf = session_factory
        self._t = tables
        self._id = id_factory
        self._clock = clock

    def find_by_email(self, *, organization_id: str, email: str) -> PasswordCredential | None:
        table = self._t.password_credential
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id, table.c.email == email
                )
            ).first()
        return None if row is None else _row_to_credential(row)

    def create_account(
        self,
        *,
        organization_id: str,
        email: str,
        password_hash: str,
        email_verified: bool = False,
        is_admin: bool = False,
    ) -> PasswordCredential:
        now = self._clock()
        subject_id = self._id()
        user_id = self._id()
        try:
            with SqlAlchemyUnitOfWork(self._sf) as uow:
                set_tenant_guc(uow.session, organization_id)
                uow.session.execute(
                    insert(self._t.subject).values(
                        subject_id=subject_id,
                        subject_type=SubjectType.USER.value,
                        created_at=now,
                        tenant_scope=organization_id,
                    )
                )
                uow.session.execute(
                    insert(self._t.user_account).values(
                        user_id=user_id,
                        subject_id=subject_id,
                        primary_email=email,
                        display_name=None,
                        created_at=now,
                    )
                )
                uow.session.execute(
                    insert(self._t.password_credential).values(
                        user_id=user_id,
                        subject_id=subject_id,
                        organization_id=organization_id,
                        email=email,
                        password_hash=password_hash,
                        email_verified=email_verified,
                        is_admin=is_admin,
                        created_at=now,
                        updated_at=now,
                    )
                )
                uow.commit()
        except IntegrityError as exc:
            raise LocalAuthError("email already registered") from exc
        return PasswordCredential(
            user_id=user_id,
            subject_id=subject_id,
            email=email,
            password_hash=password_hash,
            email_verified=email_verified,
            created_at=now,
            updated_at=now,
            is_admin=is_admin,
        )

    def is_admin(self, *, organization_id: str, subject_id: str) -> bool:
        table = self._t.password_credential
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.is_admin).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                )
            ).first()
        return bool(row[0]) if row else False

    def ensure_admin(
        self, *, organization_id: str, email: str, password_hash: str
    ) -> None:
        """Idempotently seed a verified admin account (create if missing, else flag admin)."""
        table = self._t.password_credential
        existing = self.find_by_email(organization_id=organization_id, email=email)
        if existing is None:
            self.create_account(
                organization_id=organization_id,
                email=email,
                password_hash=password_hash,
                email_verified=True,
                is_admin=True,
            )
            return
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == existing.subject_id,
                )
                .values(is_admin=True, email_verified=True, updated_at=self._clock())
            )
            uow.commit()

    def set_verified(self, *, organization_id: str, subject_id: str) -> None:
        table = self._t.password_credential
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(table.c.organization_id == organization_id, table.c.subject_id == subject_id)
                .values(email_verified=True, updated_at=self._clock())
            )
            uow.commit()

    def set_password(
        self, *, organization_id: str, subject_id: str, password_hash: str
    ) -> None:
        table = self._t.password_credential
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(table.c.organization_id == organization_id, table.c.subject_id == subject_id)
                .values(password_hash=password_hash, updated_at=self._clock())
            )
            uow.commit()


class SqlAlchemyVerificationTokenStore:
    """Persists single-use, expiring verification tokens by SHA-256."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: IdentityTables
    ) -> None:
        self._sf = session_factory
        self._t = tables

    def save(self, *, organization_id: str, token: VerificationToken) -> None:
        table = self._t.verification_token
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    token_id=token.token_id,
                    organization_id=organization_id,
                    token_sha256=token.token_sha256,
                    purpose=token.purpose.value,
                    subject_id=token.subject_id,
                    email=token.email,
                    created_at=token.created_at,
                    expires_at=token.expires_at,
                    consumed_at=token.consumed_at,
                )
            )
            uow.commit()

    def find_by_hash(
        self, *, organization_id: str, token_sha256: str
    ) -> VerificationToken | None:
        table = self._t.verification_token
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.token_sha256 == token_sha256,
                )
            ).first()
        return None if row is None else _row_to_token(row)

    def consume(self, *, organization_id: str, token_id: str, now: datetime) -> None:
        table = self._t.verification_token
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(table.c.organization_id == organization_id, table.c.token_id == token_id)
                .values(consumed_at=now)
            )
            uow.commit()


class SqlAlchemyAccountEventStore:
    """Durable append-only account-activity log."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: IdentityTables
    ) -> None:
        self._sf = session_factory
        self._t = tables

    def record(self, *, event: AccountEvent) -> None:
        table = self._t.account_event
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, event.organization_id)
            uow.session.execute(
                insert(table).values(
                    event_id=event.event_id,
                    organization_id=event.organization_id,
                    subject_id=event.subject_id,
                    event_type=event.event_type.value,
                    detail=event.detail,
                    created_at=event.created_at,
                )
            )
            uow.commit()

    def list_for_subject(
        self, *, organization_id: str, subject_id: str, limit: int = 50
    ) -> Sequence[AccountEvent]:
        table = self._t.account_event
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                )
                .order_by(table.c.created_at.desc())
                .limit(limit)
            ).all()
        return [_row_to_event(r) for r in rows]

    def list_for_tenant(
        self,
        *,
        organization_id: str,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        detail_query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[AccountEvent]:
        table = self._t.account_event
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            stmt = select(table).where(
                *_activity_filters(
                    table,
                    organization_id=organization_id,
                    event_type=event_type,
                    detail_query=detail_query,
                    created_after=created_after,
                    created_before=created_before,
                )
            )
            rows = session.execute(
                stmt.order_by(table.c.created_at.desc()).limit(limit).offset(offset)
            ).all()
        return [_row_to_event(r) for r in rows]

    def count_for_tenant(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        detail_query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        table = self._t.account_event
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            counted = session.execute(
                select(func.count())
                .select_from(table)
                .where(
                    *_activity_filters(
                        table,
                        organization_id=organization_id,
                        event_type=event_type,
                        detail_query=detail_query,
                        created_after=created_after,
                        created_before=created_before,
                    )
                )
            ).scalar()
        return int(counted or 0)


def _like_pattern(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _activity_filters(
    table: Table,
    *,
    organization_id: str,
    event_type: str | None,
    detail_query: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [table.c.organization_id == organization_id]
    if event_type:
        clauses.append(table.c.event_type == event_type)
    if detail_query:
        pattern = _like_pattern(detail_query)
        clauses.append(
            or_(
                table.c.detail.ilike(pattern, escape="\\"),
                table.c.event_type.ilike(pattern, escape="\\"),
                table.c.subject_id.ilike(pattern, escape="\\"),
            )
        )
    if created_after is not None:
        clauses.append(table.c.created_at >= created_after)
    if created_before is not None:
        clauses.append(table.c.created_at <= created_before)
    return clauses


def _row_to_credential(row: object) -> PasswordCredential:
    return PasswordCredential(
        user_id=row.user_id,
        subject_id=row.subject_id,
        email=row.email,
        password_hash=row.password_hash,
        email_verified=bool(row.email_verified),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        is_admin=bool(getattr(row, "is_admin", False)),
    )


def _row_to_token(row: object) -> VerificationToken:
    return VerificationToken(
        token_id=row.token_id,
        token_sha256=row.token_sha256,
        purpose=VerificationPurpose(row.purpose),
        subject_id=row.subject_id,
        email=row.email,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        consumed_at=_aware(row.consumed_at) if row.consumed_at is not None else None,
    )


def _row_to_event(row: object) -> AccountEvent:
    return AccountEvent(
        event_id=row.event_id,
        subject_id=row.subject_id,
        organization_id=row.organization_id,
        event_type=AccountEventType(row.event_type),
        created_at=_aware(row.created_at),
        detail=row.detail,
    )
