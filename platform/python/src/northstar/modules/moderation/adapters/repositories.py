"""Moderation repositories (in-memory + SQLAlchemy) implementing :class:`ModerationRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). The case row carries the authoritative aggregate (reports/decision/enforcement/appeal
as JSONB); every transition also appends an immutable ``moderation_event`` row so the lifecycle
trail is auditable and a reversed enforcement is evidenced (FR-ANN-007, LAW-14). No string
interpolation of values.
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
    Appeal,
    AppealResolution,
    CaseEvent,
    CaseState,
    Decision,
    Disposition,
    EnforcementAction,
    EnforcementKind,
    ModerationCase,
    Report,
    ReportableRef,
)
from .tables import ModerationTables

_TERMINAL_STATE = CaseState.APPEAL_RESOLVED


# ---------------------------------------------------------------------------
# (De)serialisation helpers (JSONB projections of the aggregate)
# ---------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any] | None) -> Actor | None:
    if ref is None:
        return None
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _report_dict(report: Report) -> dict[str, Any]:
    return {
        "report_id": report.report_id,
        "reporter_id": report.reporter_id,
        "reason": report.reason,
        "created_at": report.created_at.isoformat(),
    }


def _report_from_dict(data: dict[str, Any]) -> Report:
    return Report(
        report_id=data["report_id"],
        reporter_id=data["reporter_id"],
        reason=data["reason"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _decision_dict(decision: Decision) -> dict[str, Any]:
    return {
        "disposition": decision.disposition.value,
        "rationale": decision.rationale,
        "decided_by": _actor_ref(decision.decided_by),
        "decided_at": decision.decided_at.isoformat(),
    }


def _decision_from_dict(data: dict[str, Any] | None) -> Decision | None:
    if data is None:
        return None
    return Decision(
        disposition=Disposition(data["disposition"]),
        rationale=data["rationale"],
        decided_by=_actor_from_ref(data["decided_by"]),  # type: ignore[arg-type]
        decided_at=datetime.fromisoformat(data["decided_at"]),
    )


def _enforcement_dict(enforcement: EnforcementAction) -> dict[str, Any]:
    return {
        "kind": enforcement.kind.value,
        "applied": enforcement.applied,
        "applied_by": _actor_ref(enforcement.applied_by),
        "applied_at": enforcement.applied_at.isoformat(),
        "receipt": enforcement.receipt,
        "reversed": enforcement.reversed,
        "reversed_by": (_actor_ref(enforcement.reversed_by) if enforcement.reversed_by else None),
        "reversed_at": (enforcement.reversed_at.isoformat() if enforcement.reversed_at else None),
    }


def _enforcement_from_dict(data: dict[str, Any] | None) -> EnforcementAction | None:
    if data is None:
        return None
    return EnforcementAction(
        kind=EnforcementKind(data["kind"]),
        applied=data["applied"],
        applied_by=_actor_from_ref(data["applied_by"]),  # type: ignore[arg-type]
        applied_at=datetime.fromisoformat(data["applied_at"]),
        receipt=data.get("receipt"),
        reversed=data.get("reversed", False),
        reversed_by=_actor_from_ref(data.get("reversed_by")),
        reversed_at=(
            datetime.fromisoformat(data["reversed_at"]) if data.get("reversed_at") else None
        ),
    )


def _appeal_dict(appeal: Appeal) -> dict[str, Any]:
    return {
        "appeal_id": appeal.appeal_id,
        "appellant_id": appeal.appellant_id,
        "rationale": appeal.rationale,
        "created_at": appeal.created_at.isoformat(),
        "resolution": appeal.resolution.value if appeal.resolution else None,
        "resolved_by": _actor_ref(appeal.resolved_by) if appeal.resolved_by else None,
        "resolved_at": appeal.resolved_at.isoformat() if appeal.resolved_at else None,
        "resolution_rationale": appeal.resolution_rationale,
    }


def _appeal_from_dict(data: dict[str, Any] | None) -> Appeal | None:
    if data is None:
        return None
    resolution = data.get("resolution")
    return Appeal(
        appeal_id=data["appeal_id"],
        appellant_id=data["appellant_id"],
        rationale=data["rationale"],
        created_at=datetime.fromisoformat(data["created_at"]),
        resolution=AppealResolution(resolution) if resolution else None,
        resolved_by=_actor_from_ref(data.get("resolved_by")),
        resolved_at=(
            datetime.fromisoformat(data["resolved_at"]) if data.get("resolved_at") else None
        ),
        resolution_rationale=data.get("resolution_rationale"),
    )


def _case_values(case: ModerationCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "organization_id": case.organization_id,
        "content_type": case.target.content_type,
        "content_id": case.target.content_id,
        "author_id": case.target.author_id,
        "state": case.state.value,
        "reports": [_report_dict(r) for r in case.reports],
        "assignee_id": case.assignee_id,
        "decision": _decision_dict(case.decision) if case.decision else None,
        "enforcement": _enforcement_dict(case.enforcement) if case.enforcement else None,
        "appeal": _appeal_dict(case.appeal) if case.appeal else None,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def _mutable_case_values(case: ModerationCase) -> dict[str, Any]:
    return {
        "state": case.state.value,
        "reports": [_report_dict(r) for r in case.reports],
        "assignee_id": case.assignee_id,
        "decision": _decision_dict(case.decision) if case.decision else None,
        "enforcement": _enforcement_dict(case.enforcement) if case.enforcement else None,
        "appeal": _appeal_dict(case.appeal) if case.appeal else None,
        "updated_at": case.updated_at,
    }


def _case_from_row(row: Any) -> ModerationCase:  # noqa: ANN401 SQLAlchemy Row is dynamic
    return ModerationCase(
        case_id=row.case_id,
        organization_id=row.organization_id,
        target=ReportableRef(
            content_type=row.content_type,
            content_id=row.content_id,
            author_id=row.author_id,
        ),
        state=CaseState(row.state),
        reports=tuple(_report_from_dict(r) for r in (row.reports or ())),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        assignee_id=row.assignee_id,
        decision=_decision_from_dict(row.decision),
        enforcement=_enforcement_from_dict(row.enforcement),
        appeal=_appeal_from_dict(row.appeal),
    )


def _event_values(event: CaseEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "case_id": event.case_id,
        "organization_id": event.organization_id,
        "action": event.action,
        "from_state": event.from_state.value if event.from_state else None,
        "to_state": event.to_state.value,
        "actor": _actor_ref(event.actor),
        "rationale": event.rationale,
        "created_at": event.created_at,
    }


def _event_from_row(row: Any) -> CaseEvent:  # noqa: ANN401 SQLAlchemy Row is dynamic
    return CaseEvent(
        event_id=row.event_id,
        case_id=row.case_id,
        organization_id=row.organization_id,
        action=row.action,
        from_state=CaseState(row.from_state) if row.from_state else None,
        to_state=CaseState(row.to_state),
        actor=_actor_from_ref(row.actor),  # type: ignore[arg-type]
        created_at=_aware(row.created_at),
        rationale=row.rationale,
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class InMemoryModerationRepository:
    """In-memory repository for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._cases: dict[str, ModerationCase] = {}
        self._events: list[CaseEvent] = []

    def add_case(self, case: ModerationCase, event: CaseEvent) -> None:
        self._cases[case.case_id] = case
        self._events.append(event)

    def get_case(self, *, organization_id: str, case_id: str) -> ModerationCase | None:
        case = self._cases.get(case_id)
        if case is None or case.organization_id != organization_id:
            return None
        return case

    def update_case(self, case: ModerationCase, event: CaseEvent) -> None:
        existing = self.get_case(organization_id=case.organization_id, case_id=case.case_id)
        if existing is None:
            return
        self._cases[case.case_id] = case
        self._events.append(event)

    def find_open_case_for_target(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ModerationCase | None:
        candidates = [
            case
            for case in self._cases.values()
            if case.organization_id == organization_id
            and case.target.content_type == content_type
            and case.target.content_id == content_id
            and case.state is not _TERMINAL_STATE
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: c.created_at)[-1]

    def list_events(self, *, organization_id: str, case_id: str) -> Sequence[CaseEvent]:
        return [
            event
            for event in self._events
            if event.organization_id == organization_id and event.case_id == case_id
        ]


class SqlAlchemyModerationRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: ModerationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_case(self, case: ModerationCase, event: CaseEvent) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, case.organization_id)
            uow.session.execute(insert(self._tables.case).values(**_case_values(case)))
            uow.session.execute(insert(self._tables.event).values(**_event_values(event)))
            uow.commit()

    def get_case(self, *, organization_id: str, case_id: str) -> ModerationCase | None:
        table = self._tables.case
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.case_id == case_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return _case_from_row(row)

    def update_case(self, case: ModerationCase, event: CaseEvent) -> None:
        table = self._tables.case
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, case.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.case_id == case.case_id,
                    table.c.organization_id == case.organization_id,
                )
                .values(**_mutable_case_values(case))
            )
            uow.session.execute(insert(self._tables.event).values(**_event_values(event)))
            uow.commit()

    def find_open_case_for_target(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ModerationCase | None:
        table = self._tables.case
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.content_type == content_type,
                    table.c.content_id == content_id,
                    table.c.state != _TERMINAL_STATE.value,
                )
                .order_by(table.c.created_at.desc())
                .limit(1)
            ).first()
        if row is None:
            return None
        return _case_from_row(row)

    def list_events(self, *, organization_id: str, case_id: str) -> Sequence[CaseEvent]:
        table = self._tables.event
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.case_id == case_id,
                )
                .order_by(table.c.created_at.asc())
            ).all()
        return [_event_from_row(row) for row in rows]
