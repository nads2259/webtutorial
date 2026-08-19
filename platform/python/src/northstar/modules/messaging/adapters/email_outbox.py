"""Durable transactional-email outbox stores (in-memory + SQLAlchemy).

The outbox is the "dev mailbox": every rendered transactional email is recorded here with its delivery
status, so an admin can view (and in dev, click the confirm/reset link from) sent mail. Tenant-scoped
by ``organization_id`` with the per-transaction tenant GUC set for FORCED RLS (rule 50).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Table, func, insert, or_, select
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..application.transactional import EmailMessage, EmailStatus
from .tables import MessagingTables


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class InMemoryEmailOutbox:
    """In-memory outbox for tests and non-durable contexts."""

    def __init__(self) -> None:
        self._rows: list[EmailMessage] = []

    def record(self, *, message: EmailMessage) -> None:
        self._rows.append(message)

    def list_recent(
        self,
        *,
        organization_id: str,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[EmailMessage]:
        needle = (q or "").strip().lower()
        rows = [
            m
            for m in self._rows
            if m.organization_id == organization_id
            and (not status or m.status.value == status)
            and (
                not needle
                or needle in m.to_email.lower()
                or needle in m.subject.lower()
                or needle in (m.template_id or "").lower()
            )
            and (created_after is None or m.created_at >= created_after)
            and (created_before is None or m.created_at <= created_before)
        ]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        return rows[offset : offset + limit]

    def count_recent(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        return len(
            self.list_recent(
                organization_id=organization_id,
                limit=10_000_000,
                offset=0,
                status=status,
                q=q,
                created_after=created_after,
                created_before=created_before,
            )
        )


class SqlAlchemyEmailOutbox:
    """Durable PostgreSQL outbox; every query filters by ``organization_id`` and sets the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: MessagingTables
    ) -> None:
        self._sf = session_factory
        self._t = tables

    def record(self, *, message: EmailMessage) -> None:
        table = self._t.email_message
        with SqlAlchemyUnitOfWork(self._sf) as uow:
            set_tenant_guc(uow.session, message.organization_id)
            uow.session.execute(
                insert(table).values(
                    message_id=message.message_id,
                    organization_id=message.organization_id,
                    to_email=message.to_email,
                    template_id=message.template_id,
                    subject=message.subject,
                    html_body=message.html_body,
                    text_body=message.text_body,
                    status=message.status.value,
                    provider_message_id=message.provider_message_id,
                    error=message.error,
                    created_at=message.created_at,
                )
            )
            uow.commit()

    def list_recent(
        self,
        *,
        organization_id: str,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[EmailMessage]:
        table = self._t.email_message
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            stmt = (
                select(table)
                .where(
                    *_outbox_filters(
                        table,
                        organization_id=organization_id,
                        status=status,
                        q=q,
                        created_after=created_after,
                        created_before=created_before,
                    )
                )
                .order_by(table.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = session.execute(stmt).all()
        return [
            EmailMessage(
                message_id=r.message_id,
                organization_id=r.organization_id,
                to_email=r.to_email,
                template_id=r.template_id,
                subject=r.subject,
                html_body=r.html_body,
                text_body=r.text_body,
                status=EmailStatus(r.status),
                created_at=_aware(r.created_at),
                provider_message_id=r.provider_message_id,
                error=r.error,
            )
            for r in rows
        ]

    def count_recent(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        table = self._t.email_message
        with self._sf() as session:
            set_tenant_guc(session, organization_id)
            counted = session.execute(
                select(func.count())
                .select_from(table)
                .where(
                    *_outbox_filters(
                        table,
                        organization_id=organization_id,
                        status=status,
                        q=q,
                        created_after=created_after,
                        created_before=created_before,
                    )
                )
            ).scalar()
        return int(counted or 0)


def _like_pattern(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _outbox_filters(
    table: Table,
    *,
    organization_id: str,
    status: str | None,
    q: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [table.c.organization_id == organization_id]
    if status:
        clauses.append(table.c.status == status)
    if q:
        pattern = _like_pattern(q)
        clauses.append(
            or_(
                table.c.to_email.ilike(pattern, escape="\\"),
                table.c.subject.ilike(pattern, escape="\\"),
                table.c.template_id.ilike(pattern, escape="\\"),
            )
        )
    if created_after is not None:
        clauses.append(table.c.created_at >= created_after)
    if created_before is not None:
        clauses.append(table.c.created_at <= created_before)
    return clauses
