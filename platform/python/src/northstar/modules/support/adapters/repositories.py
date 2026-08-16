"""Support repositories (in-memory + SQLAlchemy) implementing :class:`SupportRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. Messages are stored separately from case metadata and re-attached on read; the
access-audit log is append-only tamper-evident evidence (FR-SUP-003).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import (
    AuthorType,
    CasePriority,
    CaseStatus,
    MessageVisibility,
    SupportAccessGrant,
    SupportCase,
    SupportMessage,
)
from .tables import SupportTables


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemorySupportRepository:
    """In-memory, tenant-scoped support repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._cases: dict[tuple[str, str], SupportCase] = {}
        self._messages: dict[tuple[str, str], list[SupportMessage]] = {}
        self._grants: dict[tuple[str, str], SupportAccessGrant] = {}
        self._log: list[dict[str, object]] = []

    def add_case(self, *, organization_id: str, case: SupportCase) -> None:
        self._cases[(organization_id, case.case_id)] = case.with_messages(())

    def get_case(self, *, organization_id: str, case_id: str) -> SupportCase | None:
        case = self._cases.get((organization_id, case_id))
        if case is None:
            return None
        messages = tuple(self._messages.get((organization_id, case_id), ()))
        return case.with_messages(messages)

    def save_case(self, *, organization_id: str, case: SupportCase) -> None:
        self._cases[(organization_id, case.case_id)] = case.with_messages(())

    def add_message(
        self, *, organization_id: str, case_id: str, message: SupportMessage, body: str
    ) -> None:
        self._messages.setdefault((organization_id, case_id), []).append(message)

    def add_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None:
        self._grants[(organization_id, grant.grant_id)] = grant

    def get_grant(self, *, organization_id: str, grant_id: str) -> SupportAccessGrant | None:
        return self._grants.get((organization_id, grant_id))

    def save_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None:
        self._grants[(organization_id, grant.grant_id)] = grant

    def active_grant_for(
        self, *, organization_id: str, case_id: str, staff_id: str, now: datetime
    ) -> SupportAccessGrant | None:
        for (org, _gid), grant in self._grants.items():
            if (
                org == organization_id
                and grant.case_id == case_id
                and grant.staff_id == staff_id
                and grant.is_active(now)
            ):
                return grant
        return None

    def record_access(
        self,
        *,
        organization_id: str,
        log_id: str,
        case_id: str,
        staff_id: str,
        scope: str,
        decision: str,
        now: datetime,
    ) -> None:
        self._log.append(
            {
                "organization_id": organization_id,
                "log_id": log_id,
                "case_id": case_id,
                "staff_id": staff_id,
                "scope": scope,
                "decision": decision,
                "occurred_at": now,
            }
        )

    def list_access_log(self, *, organization_id: str, case_id: str) -> Sequence[dict[str, object]]:
        return [
            entry
            for entry in self._log
            if entry["organization_id"] == organization_id and entry["case_id"] == case_id
        ]


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemySupportRepository:
    """PostgreSQL support repository; every query filters by ``organization_id`` + sets the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: SupportTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_case(self, *, organization_id: str, case: SupportCase) -> None:
        table = self._tables.support_case
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    **_case_values(organization_id, case), created_at=_aware(case.created_at)
                )
            )
            uow.commit()

    def save_case(self, *, organization_id: str, case: SupportCase) -> None:
        table = self._tables.support_case
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.case_id == case.case_id,
                )
                .values(
                    status=case.status.value,
                    priority=case.priority.value,
                    category=case.category,
                    subject=case.subject,
                    assignee_id=case.assignee_id,
                    updated_at=(_aware(case.updated_at) if case.updated_at else None),
                    retention_policy=case.retention_policy,
                    related_resources=[dict(r) for r in case.related_resources],
                )
            )
            uow.commit()

    def get_case(self, *, organization_id: str, case_id: str) -> SupportCase | None:
        cases = self._tables.support_case
        messages = self._tables.support_message
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(cases).where(
                    cases.c.organization_id == organization_id,
                    cases.c.case_id == case_id,
                )
            ).first()
            if row is None:
                return None
            message_rows = session.execute(
                select(messages)
                .where(
                    messages.c.organization_id == organization_id,
                    messages.c.case_id == case_id,
                )
                .order_by(messages.c.created_at)
            ).all()
        loaded = tuple(_row_to_message(m) for m in message_rows)
        return _row_to_case(row).with_messages(loaded)

    def add_message(
        self, *, organization_id: str, case_id: str, message: SupportMessage, body: str
    ) -> None:
        table = self._tables.support_message
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    message_id=message.message_id,
                    case_id=case_id,
                    author_type=message.author_type.value,
                    body_ref=message.body_ref,
                    body=body,
                    visibility=message.visibility.value,
                    created_at=_aware(message.created_at),
                )
            )
            uow.commit()

    def add_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None:
        table = self._tables.support_access_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(**_grant_values(organization_id, grant), created_at=_now())
            )
            uow.commit()

    def get_grant(self, *, organization_id: str, grant_id: str) -> SupportAccessGrant | None:
        table = self._tables.support_access_grant
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.grant_id == grant_id,
                )
            ).first()
        return None if row is None else _row_to_grant(row)

    def save_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None:
        table = self._tables.support_access_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.grant_id == grant.grant_id,
                )
                .values(
                    revoked=grant.revoked_at is not None,
                    revoked_at=(_aware(grant.revoked_at) if grant.revoked_at else None),
                )
            )
            uow.commit()

    def active_grant_for(
        self, *, organization_id: str, case_id: str, staff_id: str, now: datetime
    ) -> SupportAccessGrant | None:
        table = self._tables.support_access_grant
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.case_id == case_id,
                    table.c.staff_id == staff_id,
                    table.c.revoked.is_(False),
                )
            ).all()
        for row in rows:
            grant = _row_to_grant(row)
            if grant.is_active(now):
                return grant
        return None

    def record_access(
        self,
        *,
        organization_id: str,
        log_id: str,
        case_id: str,
        staff_id: str,
        scope: str,
        decision: str,
        now: datetime,
    ) -> None:
        table = self._tables.support_access_log
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    log_id=log_id,
                    case_id=case_id,
                    staff_id=staff_id,
                    scope=scope,
                    decision=decision,
                    occurred_at=_aware(now),
                )
            )
            uow.commit()

    def list_access_log(self, *, organization_id: str, case_id: str) -> Sequence[dict[str, object]]:
        table = self._tables.support_access_log
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.case_id == case_id,
                )
            ).all()
        return [
            {
                "log_id": row.log_id,
                "case_id": row.case_id,
                "staff_id": row.staff_id,
                "scope": row.scope,
                "decision": row.decision,
                "occurred_at": _aware(row.occurred_at),
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Row/value mappers
# ---------------------------------------------------------------------------


def _case_values(organization_id: str, case: SupportCase) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "case_id": case.case_id,
        "requester_id": case.requester_id,
        "assignee_id": case.assignee_id,
        "status": case.status.value,
        "priority": case.priority.value,
        "category": case.category,
        "subject": case.subject,
        "audit_scope": case.audit_scope,
        "retention_policy": case.retention_policy,
        "related_resources": [dict(r) for r in case.related_resources],
        "updated_at": (_aware(case.updated_at) if case.updated_at else None),
    }


def _grant_values(organization_id: str, grant: SupportAccessGrant) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "grant_id": grant.grant_id,
        "case_id": grant.case_id,
        "staff_id": grant.staff_id,
        "granted_by": grant.granted_by,
        "reason": grant.reason,
        "scope": grant.scope,
        "starts_at": _aware(grant.starts_at),
        "expires_at": _aware(grant.expires_at),
        "revoked": grant.revoked_at is not None,
        "revoked_at": (_aware(grant.revoked_at) if grant.revoked_at else None),
    }


def _row_to_case(row: object) -> SupportCase:
    return SupportCase(
        case_id=row.case_id,
        requester_id=row.requester_id,
        status=CaseStatus(row.status),
        priority=CasePriority(row.priority),
        category=row.category,
        created_at=_aware(row.created_at),
        audit_scope=row.audit_scope,
        subject=row.subject,
        organization_id=row.organization_id,
        assignee_id=row.assignee_id,
        updated_at=(_aware(row.updated_at) if row.updated_at else None),
        retention_policy=row.retention_policy,
        related_resources=tuple(dict(r) for r in (row.related_resources or ())),
    )


def _row_to_message(row: object) -> SupportMessage:
    return SupportMessage(
        message_id=row.message_id,
        author_type=AuthorType(row.author_type),
        body_ref=row.body_ref,
        visibility=MessageVisibility(row.visibility),
        created_at=_aware(row.created_at),
    )


def _row_to_grant(row: object) -> SupportAccessGrant:
    return SupportAccessGrant(
        grant_id=row.grant_id,
        case_id=row.case_id,
        staff_id=row.staff_id,
        granted_by=row.granted_by,
        reason=row.reason,
        starts_at=_aware(row.starts_at),
        expires_at=_aware(row.expires_at),
        scope=row.scope,
        revoked_at=(_aware(row.revoked_at) if row.revoked_at else None),
    )


__all__ = [
    "InMemorySupportRepository",
    "SqlAlchemySupportRepository",
]
