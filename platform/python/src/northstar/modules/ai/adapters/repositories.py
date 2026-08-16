"""AI repositories (in-memory + SQLAlchemy) for the prompt registry, memory and traces.

Every implementation honors the governance invariants: the prompt registry is IMMUTABLE (a
``(package_id, version)`` is written once, never mutated — FR-AI-002); memory and traces are
tenant-scoped with the per-transaction tenant GUC set so PostgreSQL RLS denies foreign-tenant rows
as defense-in-depth (rule 50). Values are bound parameters via SQLAlchemy Core expressions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.budgets import BudgetLimit, BudgetScope, CostEntry
from ..domain.errors import PromptPackageImmutable, PromptPackageNotFound
from ..domain.model import (
    ActorProfile,
    InteractionTrace,
    MemoryClass,
    MemoryPolicy,
    MemoryRecord,
    PromptPackage,
    PromptPackageRef,
    ToolCallRecord,
)
from .tables import AiTables

_Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Prompt registry
# ---------------------------------------------------------------------------


class InMemoryPromptRegistry:
    """In-memory immutable prompt registry for fast unit tests (FR-AI-002)."""

    def __init__(self) -> None:
        self._packages: dict[tuple[str, str], PromptPackage] = {}

    def register(self, package: PromptPackage) -> None:
        key = (package.package_id, package.version)
        if key in self._packages:
            raise PromptPackageImmutable(package.package_id, package.version)
        self._packages[key] = package

    def get(self, ref: PromptPackageRef) -> PromptPackage:
        package = self._packages.get((ref.package_id, ref.version))
        if package is None:
            raise PromptPackageNotFound(ref.package_id, ref.version)
        return package


class SqlAlchemyPromptRegistry:
    """PostgreSQL immutable prompt registry: a version is inserted once, never updated."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: AiTables,
        clock: _Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._table = tables.prompt_package
        self._clock = clock

    def register(self, package: PromptPackage) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            existing = uow.session.execute(
                select(self._table.c.package_id).where(
                    self._table.c.package_id == package.package_id,
                    self._table.c.version == package.version,
                )
            ).first()
            if existing is not None:
                raise PromptPackageImmutable(package.package_id, package.version)
            uow.session.execute(
                insert(self._table).values(
                    package_id=package.package_id,
                    version=package.version,
                    actor_profile=package.actor_profile.value,
                    purpose=package.purpose,
                    system_instruction=package.system_instruction,
                    developer_instructions=list(package.developer_instructions),
                    declared_tools=list(package.declared_tools),
                    retrieval_profile=package.retrieval_profile,
                    memory_policy=package.memory_policy.value,
                    evaluation_suite=package.evaluation_suite,
                    status=package.status,
                    created_at=self._clock(),
                )
            )
            uow.commit()

    def get(self, ref: PromptPackageRef) -> PromptPackage:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(self._table).where(
                        self._table.c.package_id == ref.package_id,
                        self._table.c.version == ref.version,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise PromptPackageNotFound(ref.package_id, ref.version)
        return PromptPackage(
            package_id=row["package_id"],
            version=row["version"],
            actor_profile=ActorProfile(row["actor_profile"]),
            purpose=row["purpose"],
            system_instruction=row["system_instruction"],
            developer_instructions=tuple(row["developer_instructions"]),
            declared_tools=tuple(row["declared_tools"]),
            retrieval_profile=row["retrieval_profile"],
            memory_policy=MemoryPolicy(row["memory_policy"]),
            evaluation_suite=row["evaluation_suite"],
            status=row["status"],
        )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class InMemoryMemoryRepository:
    """In-memory purpose-limited memory store (tenant + owner scoped)."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def add(self, record: MemoryRecord) -> None:
        self._records[record.memory_id] = record

    def get(self, *, organization_id: str, owner_id: str, memory_id: str) -> MemoryRecord | None:
        record = self._records.get(memory_id)
        if record is None:
            return None
        if record.organization_id != organization_id or record.owner_id != owner_id:
            return None
        return record

    def _owned(self, *, organization_id: str, owner_id: str) -> list[MemoryRecord]:
        return [
            record
            for record in self._records.values()
            if record.organization_id == organization_id and record.owner_id == owner_id
        ]

    def list_for_owner(self, *, organization_id: str, owner_id: str) -> Sequence[MemoryRecord]:
        return [
            record
            for record in self._owned(organization_id=organization_id, owner_id=owner_id)
            if record.active
        ]

    def export_for_owner(self, *, organization_id: str, owner_id: str) -> Sequence[MemoryRecord]:
        return self._owned(organization_id=organization_id, owner_id=owner_id)

    def supersede(self, *, previous: MemoryRecord, correction: MemoryRecord) -> None:
        stored = self._records.get(previous.memory_id)
        if stored is not None:
            self._records[previous.memory_id] = replace(stored, superseded_by=correction.memory_id)
        self._records[correction.memory_id] = correction

    def delete(self, *, organization_id: str, owner_id: str, memory_id: str) -> bool:
        record = self.get(organization_id=organization_id, owner_id=owner_id, memory_id=memory_id)
        if record is None:
            return False
        del self._records[memory_id]
        return True

    def erase_for_owner(self, *, organization_id: str, owner_id: str) -> int:
        doomed = [
            record.memory_id
            for record in self._owned(organization_id=organization_id, owner_id=owner_id)
        ]
        for memory_id in doomed:
            del self._records[memory_id]
        return len(doomed)

    def count_for_owner(self, *, organization_id: str, owner_id: str) -> int:
        return len(self._owned(organization_id=organization_id, owner_id=owner_id))


class SqlAlchemyMemoryRepository:
    """PostgreSQL purpose-limited memory store; every operation is tenant + owner scoped."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: AiTables,
        clock: _Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._table = tables.ai_memory
        self._clock = clock

    def add(self, record: MemoryRecord) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, record.organization_id)
            uow.session.execute(insert(self._table).values(**self._row_values(record)))
            uow.commit()

    def _row_values(self, record: MemoryRecord) -> dict[str, object]:
        return {
            "memory_id": record.memory_id,
            "organization_id": record.organization_id,
            "owner_id": record.owner_id,
            "memory_class": record.memory_class.value,
            "purpose": record.purpose,
            "classification": record.classification,
            "content": record.content,
            "retention": record.retention,
            "inferred": record.inferred,
            "supersedes": record.supersedes,
            "superseded_by": record.superseded_by,
            "created_at": self._clock(),
        }

    def get(self, *, organization_id: str, owner_id: str, memory_id: str) -> MemoryRecord | None:
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = (
                session.execute(
                    select(self._table).where(
                        self._table.c.organization_id == organization_id,
                        self._table.c.owner_id == owner_id,
                        self._table.c.memory_id == memory_id,
                    )
                )
                .mappings()
                .first()
            )
        return _memory_from_row(row) if row is not None else None

    def list_for_owner(self, *, organization_id: str, owner_id: str) -> Sequence[MemoryRecord]:
        return self._select_owner(
            organization_id=organization_id, owner_id=owner_id, active_only=True
        )

    def export_for_owner(self, *, organization_id: str, owner_id: str) -> Sequence[MemoryRecord]:
        return self._select_owner(
            organization_id=organization_id, owner_id=owner_id, active_only=False
        )

    def _select_owner(
        self, *, organization_id: str, owner_id: str, active_only: bool
    ) -> list[MemoryRecord]:
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            stmt = select(self._table).where(
                self._table.c.organization_id == organization_id,
                self._table.c.owner_id == owner_id,
            )
            if active_only:
                stmt = stmt.where(self._table.c.superseded_by.is_(None))
            rows = (
                session.execute(stmt.order_by(self._table.c.created_at, self._table.c.memory_id))
                .mappings()
                .all()
            )
        return [_memory_from_row(row) for row in rows]

    def supersede(self, *, previous: MemoryRecord, correction: MemoryRecord) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, correction.organization_id)
            uow.session.execute(insert(self._table).values(**self._row_values(correction)))
            uow.session.execute(
                update(self._table)
                .where(
                    self._table.c.organization_id == previous.organization_id,
                    self._table.c.owner_id == previous.owner_id,
                    self._table.c.memory_id == previous.memory_id,
                )
                .values(superseded_by=correction.memory_id)
            )
            uow.commit()

    def delete(self, *, organization_id: str, owner_id: str, memory_id: str) -> bool:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            result = uow.session.execute(
                delete(self._table).where(
                    self._table.c.organization_id == organization_id,
                    self._table.c.owner_id == owner_id,
                    self._table.c.memory_id == memory_id,
                )
            )
            uow.commit()
        return bool(result.rowcount)

    def erase_for_owner(self, *, organization_id: str, owner_id: str) -> int:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            result = uow.session.execute(
                delete(self._table).where(
                    self._table.c.organization_id == organization_id,
                    self._table.c.owner_id == owner_id,
                )
            )
            uow.commit()
        return int(result.rowcount or 0)

    def count_for_owner(self, *, organization_id: str, owner_id: str) -> int:
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            total = session.execute(
                select(func.count())
                .select_from(self._table)
                .where(
                    self._table.c.organization_id == organization_id,
                    self._table.c.owner_id == owner_id,
                )
            ).scalar_one()
        return int(total)


def _memory_from_row(row: object) -> MemoryRecord:
    mapping = row  # SQLAlchemy RowMapping
    return MemoryRecord(
        memory_id=mapping["memory_id"],  # type: ignore[index]
        organization_id=mapping["organization_id"],  # type: ignore[index]
        owner_id=mapping["owner_id"],  # type: ignore[index]
        memory_class=MemoryClass(mapping["memory_class"]),  # type: ignore[index]
        purpose=mapping["purpose"],  # type: ignore[index]
        classification=mapping["classification"],  # type: ignore[index]
        content=mapping["content"],  # type: ignore[index]
        retention=mapping["retention"],  # type: ignore[index]
        inferred=bool(mapping["inferred"]),  # type: ignore[index]
        supersedes=mapping["supersedes"],  # type: ignore[index]
        superseded_by=mapping["superseded_by"],  # type: ignore[index]
    )


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


class InMemoryTraceRepository:
    """In-memory interaction-trace store (provenance, FR-AI-009)."""

    def __init__(self) -> None:
        self.traces: list[InteractionTrace] = []

    def record(self, trace: InteractionTrace) -> None:
        self.traces.append(trace)


class SqlAlchemyTraceRepository:
    """PostgreSQL interaction-trace store; tenant-scoped writes for provenance (FR-AI-009)."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: AiTables,
        clock: _Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._table = tables.ai_trace
        self._clock = clock

    def record(self, trace: InteractionTrace) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, trace.organization_id)
            uow.session.execute(
                insert(self._table).values(
                    trace_id=trace.trace_id,
                    organization_id=trace.organization_id,
                    actor_id=trace.actor_id,
                    actor_profile=trace.actor_profile.value,
                    provider=trace.provider,
                    model=trace.model,
                    prompt_package=trace.prompt_package,
                    input_tokens=trace.usage.input_tokens,
                    output_tokens=trace.usage.output_tokens,
                    cost_units=trace.usage.cost_units,
                    tool_calls=json.loads(json.dumps([_tool_record(r) for r in trace.tool_calls])),
                    citations_valid=trace.citations_valid,
                    citations_rejected=trace.citations_rejected,
                    refused=trace.refused,
                    created_at=self._clock(),
                )
            )
            uow.commit()


def _tool_record(record: ToolCallRecord) -> dict[str, object]:
    return {
        "tool_id": record.tool_id,
        "outcome": record.outcome,
        "reason_code": record.reason_code,
        "cost_units": record.cost_units,
    }


# ---------------------------------------------------------------------------
# Budget ledger (multi-scope cost budgets + recorded provider costs, FR-AI-008)
# ---------------------------------------------------------------------------


def _applicable_limit_order(
    limits_by_key: dict[tuple[str, str], BudgetLimit],
    *,
    organization_id: str,
    actor_id: str,
    workflow_id: str | None,
) -> list[BudgetLimit]:
    """Return the applicable budgets MOST-SPECIFIC-FIRST (workflow, then actor, then tenant)."""
    ordered: list[BudgetLimit] = []
    if workflow_id is not None:
        wf = limits_by_key.get((BudgetScope.WORKFLOW.value, workflow_id))
        if wf is not None:
            ordered.append(wf)
    actor = limits_by_key.get((BudgetScope.ACTOR.value, actor_id))
    if actor is not None:
        ordered.append(actor)
    tenant = limits_by_key.get((BudgetScope.TENANT.value, organization_id))
    if tenant is not None:
        ordered.append(tenant)
    return ordered


def _entry_matches_scope(
    entry: CostEntry, *, organization_id: str, scope: str, scope_id: str
) -> bool:
    if entry.organization_id != organization_id:
        return False
    if scope == BudgetScope.TENANT.value:
        return scope_id == organization_id
    if scope == BudgetScope.ACTOR.value:
        return entry.actor_id == scope_id
    if scope == BudgetScope.WORKFLOW.value:
        return entry.workflow_id == scope_id
    return False


class InMemoryBudgetLedger:
    """In-memory multi-scope budget ledger for fast unit tests (FR-AI-008).

    ``set_limit`` configures a budget; ``record`` appends a cost entry; ``spent``/``total_recorded``
    aggregate recorded provider cost per scope. Always tenant-scoped by ``organization_id``.
    """

    def __init__(self) -> None:
        self._limits: dict[tuple[str, tuple[str, str]], BudgetLimit] = {}
        self._entries: list[CostEntry] = []

    def set_limit(self, *, organization_id: str, limit: BudgetLimit) -> None:
        self._limits[(organization_id, limit.key)] = limit

    def limits_for(
        self, *, organization_id: str, actor_id: str, workflow_id: str | None
    ) -> Sequence[BudgetLimit]:
        by_key = {key[1]: limit for key, limit in self._limits.items() if key[0] == organization_id}
        return _applicable_limit_order(
            by_key,
            organization_id=organization_id,
            actor_id=actor_id,
            workflow_id=workflow_id,
        )

    def spent(self, *, organization_id: str, scope: str, scope_id: str) -> float:
        return sum(
            entry.cost_units
            for entry in self._entries
            if _entry_matches_scope(
                entry, organization_id=organization_id, scope=scope, scope_id=scope_id
            )
        )

    def total_recorded(self, *, organization_id: str, scope: str, scope_id: str) -> float:
        return sum(
            entry.provider_cost
            for entry in self._entries
            if _entry_matches_scope(
                entry, organization_id=organization_id, scope=scope, scope_id=scope_id
            )
        )

    def record(self, entry: CostEntry) -> None:
        self._entries.append(entry)


class SqlAlchemyBudgetLedger:
    """PostgreSQL multi-scope budget ledger; every operation is tenant-scoped under FORCED RLS."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: AiTables,
        clock: _Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._budget = tables.ai_budget
        self._ledger = tables.ai_cost_ledger
        self._clock = clock

    def set_limit(self, *, organization_id: str, budget_id: str, limit: BudgetLimit) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(self._budget).values(
                    budget_id=budget_id,
                    organization_id=organization_id,
                    scope=limit.scope.value,
                    scope_id=limit.scope_id,
                    limit_units=limit.limit_units,
                    budget_window=limit.window,
                    created_at=self._clock(),
                )
            )
            uow.commit()

    def limits_for(
        self, *, organization_id: str, actor_id: str, workflow_id: str | None
    ) -> Sequence[BudgetLimit]:
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = (
                session.execute(
                    select(self._budget).where(self._budget.c.organization_id == organization_id)
                )
                .mappings()
                .all()
            )
        by_key = {
            (row["scope"], row["scope_id"]): BudgetLimit(
                scope=BudgetScope(row["scope"]),
                scope_id=row["scope_id"],
                limit_units=float(row["limit_units"]),
                window=row["budget_window"],
            )
            for row in rows
        }
        return _applicable_limit_order(
            by_key,
            organization_id=organization_id,
            actor_id=actor_id,
            workflow_id=workflow_id,
        )

    def _sum(self, *, organization_id: str, scope: str, scope_id: str, column: str) -> float:
        col = self._ledger.c[column]
        stmt = select(func.coalesce(func.sum(col), 0.0)).where(
            self._ledger.c.organization_id == organization_id
        )
        if scope == BudgetScope.ACTOR.value:
            stmt = stmt.where(self._ledger.c.actor_id == scope_id)
        elif scope == BudgetScope.WORKFLOW.value:
            stmt = stmt.where(self._ledger.c.workflow_id == scope_id)
        elif scope != BudgetScope.TENANT.value:
            return 0.0
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            total = session.execute(stmt).scalar_one()
        return float(total)

    def spent(self, *, organization_id: str, scope: str, scope_id: str) -> float:
        return self._sum(
            organization_id=organization_id, scope=scope, scope_id=scope_id, column="cost_units"
        )

    def total_recorded(self, *, organization_id: str, scope: str, scope_id: str) -> float:
        return self._sum(
            organization_id=organization_id, scope=scope, scope_id=scope_id, column="provider_cost"
        )

    def record(self, entry: CostEntry) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, entry.organization_id)
            uow.session.execute(
                insert(self._ledger).values(
                    entry_id=entry.entry_id,
                    organization_id=entry.organization_id,
                    actor_id=entry.actor_id,
                    workflow_id=entry.workflow_id,
                    cost_units=entry.cost_units,
                    provider_cost=entry.provider_cost,
                    provider=entry.provider,
                    correlation_id=entry.correlation_id,
                    created_at=self._clock(),
                )
            )
            uow.commit()
