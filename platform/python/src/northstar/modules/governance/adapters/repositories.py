"""Governance repositories (in-memory + SQLAlchemy) implementing :class:`GovernanceRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). Decision records are append-only (the domain forbids in-place mutation — a correction
is a new superseding record). A control exception is updated ONLY to record a revocation. Actor and
link value objects are stored as JSONB projections. No string interpolation of values.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import Actor, ActorType

from ..domain.model import (
    ControlException,
    DecisionLinks,
    DecisionRecord,
    DecisionStatus,
    ExceptionStatus,
)
from .tables import GovernanceTables

# ---------------------------------------------------------------------------
# (De)serialisation helpers (JSONB projections of the value objects)
# ---------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any] | None) -> Actor | None:
    if ref is None:
        return None
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _links_dict(links: DecisionLinks) -> dict[str, Any]:
    return {
        "controls": list(links.controls),
        "requirements": list(links.requirements),
        "gates": list(links.gates),
    }


def _links_from_dict(data: dict[str, Any]) -> DecisionLinks:
    return DecisionLinks(
        controls=tuple(data.get("controls", ())),
        requirements=tuple(data.get("requirements", ())),
        gates=tuple(data.get("gates", ())),
    )


def _decision_values(decision: DecisionRecord) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "organization_id": decision.organization_id,
        "title": decision.title,
        "status": decision.status.value,
        "rationale": decision.rationale,
        "decider": _actor_ref(decision.decider),
        "links": _links_dict(decision.links),
        "supersedes": decision.supersedes,
        "recorded_at": decision.recorded_at,
    }


def _decision_from_row(row: Any) -> DecisionRecord:  # noqa: ANN401 SQLAlchemy Row is dynamic
    return DecisionRecord(
        decision_id=row.decision_id,
        organization_id=row.organization_id,
        title=row.title,
        status=DecisionStatus(row.status),
        rationale=row.rationale,
        decider=_actor_from_ref(row.decider),  # type: ignore[arg-type]
        recorded_at=_aware(row.recorded_at),
        links=_links_from_dict(row.links or {}),
        supersedes=row.supersedes,
    )


def _exception_values(exception: ControlException) -> dict[str, Any]:
    return {
        "exception_id": exception.exception_id,
        "organization_id": exception.organization_id,
        "control": exception.control,
        "subject": exception.subject,
        "approver": _actor_ref(exception.approver),
        "granted_by": _actor_ref(exception.granted_by),
        "rationale": exception.rationale,
        "status": exception.status.value,
        "expiry": exception.expiry,
        "granted_at": exception.granted_at,
        "revoked_by": _actor_ref(exception.revoked_by) if exception.revoked_by else None,
        "revoked_at": exception.revoked_at,
    }


def _mutable_exception_values(exception: ControlException) -> dict[str, Any]:
    return {
        "status": exception.status.value,
        "revoked_by": _actor_ref(exception.revoked_by) if exception.revoked_by else None,
        "revoked_at": exception.revoked_at,
    }


def _exception_from_row(row: Any) -> ControlException:  # noqa: ANN401 SQLAlchemy Row is dynamic
    return ControlException(
        exception_id=row.exception_id,
        organization_id=row.organization_id,
        control=row.control,
        subject=row.subject,
        approver=_actor_from_ref(row.approver),  # type: ignore[arg-type]
        granted_by=_actor_from_ref(row.granted_by),  # type: ignore[arg-type]
        rationale=row.rationale,
        expiry=_aware(row.expiry),
        granted_at=_aware(row.granted_at),
        status=ExceptionStatus(row.status),
        revoked_by=_actor_from_ref(row.revoked_by),
        revoked_at=_aware(row.revoked_at) if row.revoked_at else None,
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class InMemoryGovernanceRepository:
    """In-memory repository for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._decisions: dict[str, DecisionRecord] = {}
        self._exceptions: dict[str, ControlException] = {}

    def add_decision(self, decision: DecisionRecord) -> None:
        self._decisions[decision.decision_id] = decision

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord | None:
        decision = self._decisions.get(decision_id)
        if decision is None or decision.organization_id != organization_id:
            return None
        return decision

    def list_decisions(self, *, organization_id: str) -> Sequence[DecisionRecord]:
        return [
            decision
            for decision in self._decisions.values()
            if decision.organization_id == organization_id
        ]

    def add_exception(self, exception: ControlException) -> None:
        self._exceptions[exception.exception_id] = exception

    def get_exception(self, *, organization_id: str, exception_id: str) -> ControlException | None:
        exception = self._exceptions.get(exception_id)
        if exception is None or exception.organization_id != organization_id:
            return None
        return exception

    def update_exception(self, exception: ControlException) -> None:
        existing = self.get_exception(
            organization_id=exception.organization_id, exception_id=exception.exception_id
        )
        if existing is None:
            return
        self._exceptions[exception.exception_id] = exception

    def list_exceptions_for_control(
        self, *, organization_id: str, control: str
    ) -> Sequence[ControlException]:
        return [
            exception
            for exception in self._exceptions.values()
            if exception.organization_id == organization_id and exception.control == control
        ]


class SqlAlchemyGovernanceRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: GovernanceTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_decision(self, decision: DecisionRecord) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, decision.organization_id)
            uow.session.execute(insert(self._tables.decision).values(**_decision_values(decision)))
            uow.commit()

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord | None:
        table = self._tables.decision
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.decision_id == decision_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return _decision_from_row(row)

    def list_decisions(self, *, organization_id: str) -> Sequence[DecisionRecord]:
        table = self._tables.decision
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(table.c.organization_id == organization_id)
                .order_by(table.c.recorded_at.asc())
            ).all()
        return [_decision_from_row(row) for row in rows]

    def add_exception(self, exception: ControlException) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, exception.organization_id)
            uow.session.execute(
                insert(self._tables.exception).values(**_exception_values(exception))
            )
            uow.commit()

    def get_exception(self, *, organization_id: str, exception_id: str) -> ControlException | None:
        table = self._tables.exception
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.exception_id == exception_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return _exception_from_row(row)

    def update_exception(self, exception: ControlException) -> None:
        table = self._tables.exception
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, exception.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.exception_id == exception.exception_id,
                    table.c.organization_id == exception.organization_id,
                )
                .values(**_mutable_exception_values(exception))
            )
            uow.commit()

    def list_exceptions_for_control(
        self, *, organization_id: str, control: str
    ) -> Sequence[ControlException]:
        table = self._tables.exception
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.control == control,
                )
                .order_by(table.c.granted_at.asc())
            ).all()
        return [_exception_from_row(row) for row in rows]
