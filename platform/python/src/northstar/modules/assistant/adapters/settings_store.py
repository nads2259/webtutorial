"""Assistant settings stores (in-memory + SQLAlchemy) implementing :class:`AssistantSettingsPort`.

Every SQLAlchemy read/write is filtered by ``organization_id`` and sets the per-transaction tenant GUC
so PostgreSQL RLS applies as defense-in-depth (rule 50). One durable row per tenant records the admin's
active-model choice, so it survives API restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from .tables import AssistantTables


class InMemoryAssistantSettings:
    """In-memory settings for tests and non-durable contexts."""

    def __init__(self) -> None:
        self._active: dict[str, str] = {}

    def get_active_model(self, *, organization_id: str) -> str | None:
        return self._active.get(organization_id)

    def set_active_model(self, *, organization_id: str, model_id: str) -> None:
        self._active[organization_id] = model_id


class SqlAlchemyAssistantSettings:
    """Durable PostgreSQL settings; upserts one row per tenant."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: AssistantTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def get_active_model(self, *, organization_id: str) -> str | None:
        table = self._tables.setting
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.active_model).where(table.c.organization_id == organization_id)
            ).first()
        return row[0] if row else None

    def set_active_model(self, *, organization_id: str, model_id: str) -> None:
        table = self._tables.setting
        now = datetime.now(UTC)
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            existing = uow.session.execute(
                select(table.c.organization_id).where(table.c.organization_id == organization_id)
            ).first()
            if existing:
                uow.session.execute(
                    update(table)
                    .where(table.c.organization_id == organization_id)
                    .values(active_model=model_id, updated_at=now)
                )
            else:
                uow.session.execute(
                    insert(table).values(
                        organization_id=organization_id, active_model=model_id, updated_at=now
                    )
                )
            uow.commit()
