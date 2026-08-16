"""SQLAlchemy implementation of :class:`SagaStateStorePort` (durable, tenant-scoped, RLS-forced).

Every read/write is filtered by ``organization_id`` and sets the per-transaction tenant GUC so
PostgreSQL FORCED RLS applies as defense-in-depth (rule 50). Terminal saga state is persisted so a
saga id survives process restarts and re-execution is idempotent (FR-KRN-004). Step name lists are
stored as JSON arrays; the enum status is stored as its string value.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Table, insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.kernel.saga.ports import SagaRecord, SagaStateStorePort, SagaStatus

from .tenancy import set_tenant_guc
from .unit_of_work import SqlAlchemyUnitOfWork


class SqlAlchemySagaStateStore(SagaStateStorePort):
    """PostgreSQL saga-state store; every query filters by ``organization_id`` + sets the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], table: Table) -> None:
        self._session_factory = session_factory
        self._table = table

    def get(self, *, organization_id: str, saga_id: str) -> SagaRecord | None:
        table = self._table
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.saga_id == saga_id,
                )
            ).first()
        if row is None:
            return None
        return SagaRecord(
            organization_id=row.organization_id,
            saga_id=row.saga_id,
            status=SagaStatus(row.status),
            completed_steps=tuple(row.completed_steps or ()),
            compensated_steps=tuple(row.compensated_steps or ()),
            error=row.error,
        )

    def put(self, record: SagaRecord) -> None:
        table = self._table
        values = {
            "status": record.status.value,
            "completed_steps": list(record.completed_steps),
            "compensated_steps": list(record.compensated_steps),
            "error": record.error,
            "updated_at": datetime.now(UTC),
        }
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, record.organization_id)
            existing = session.execute(
                select(table.c.saga_id).where(
                    table.c.organization_id == record.organization_id,
                    table.c.saga_id == record.saga_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=record.organization_id,
                        saga_id=record.saga_id,
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == record.organization_id,
                        table.c.saga_id == record.saga_id,
                    )
                    .values(**values)
                )
            uow.commit()


__all__ = ["SqlAlchemySagaStateStore"]
