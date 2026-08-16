"""Simulation repositories (in-memory + SQLAlchemy) for definitions, tiers, scores and evidence.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. Published definitions are immutable: :meth:`publish_definition` flips a draft to
published and re-publishing/overwriting is rejected (FR-SIM-001). The ``scoring_key`` is persisted
but only ever returned via the dedicated :meth:`get_scoring_key` used by the scoring path — never as
part of a definition read. No string interpolation of values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.errors import ImmutableDefinitionError
from ..domain.model import (
    DefinitionStatus,
    EvidenceEntry,
    ResourceQuota,
    RunEvidence,
    RuntimeTier,
    Score,
    SimulationDefinition,
    TrustTier,
    definition_from_document,
)
from .tables import SimulationTables


def _now() -> datetime:
    return datetime.now(UTC)


def _definition_from_row(row: object) -> SimulationDefinition:
    from dataclasses import replace

    definition = definition_from_document(row.document, organization_id=row.organization_id)
    return replace(definition, status=DefinitionStatus(row.status))


class InMemorySimulationRepository:
    """In-memory repository for fast, deterministic unit/security tests (tenant-scoped)."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], SimulationDefinition] = {}
        self._scoring_keys: dict[tuple[str, str], str] = {}
        self._tiers: dict[tuple[str, str], TrustTier] = {}
        self._scores: dict[str, Score] = {}

    def add_definition(self, definition: SimulationDefinition, *, scoring_key: str) -> None:
        key = (definition.simulation_id, definition.version)
        existing = self._definitions.get(key)
        if existing is not None and existing.status is DefinitionStatus.PUBLISHED:
            raise ImmutableDefinitionError(definition.simulation_id, definition.version)
        self._definitions[key] = definition
        self._scoring_keys[(definition.organization_id, _sk(definition))] = scoring_key

    def get_definition(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> SimulationDefinition | None:
        definition = self._definitions.get((simulation_id, version))
        if definition is None or definition.organization_id != organization_id:
            return None
        return definition

    def publish_definition(self, definition: SimulationDefinition) -> None:
        self._definitions[(definition.simulation_id, definition.version)] = definition

    def get_scoring_key(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> str | None:
        return self._scoring_keys.get((organization_id, f"{simulation_id}@{version}"))

    def set_trust_tier(self, tier: TrustTier) -> None:
        self._tiers[(tier.organization_id, tier.tier.value)] = tier

    def get_trust_tier(self, *, organization_id: str, tier: str) -> TrustTier | None:
        return self._tiers.get((organization_id, tier))

    def add_score(self, score: Score) -> None:
        self._scores[score.run_id] = score

    def get_score(self, *, organization_id: str, run_id: str) -> Score | None:
        score = self._scores.get(run_id)
        return score if score is not None and score.organization_id == organization_id else None


def _sk(definition: SimulationDefinition) -> str:
    return f"{definition.simulation_id}@{definition.version}"


class InMemoryEvidenceStore:
    """In-memory hash-chained evidence store (tenant-scoped) for tests."""

    def __init__(self) -> None:
        self._runs: dict[str, RunEvidence] = {}

    def record(self, evidence: RunEvidence) -> None:
        self._runs[evidence.run_id] = evidence

    def get(self, *, organization_id: str, run_id: str) -> RunEvidence | None:
        evidence = self._runs.get(run_id)
        return (
            evidence
            if evidence is not None and evidence.organization_id == organization_id
            else None
        )


class SqlAlchemySimulationRepository:
    """PostgreSQL repository; every query filters by ``organization_id`` and sets the tenant GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: SimulationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_definition(self, definition: SimulationDefinition, *, scoring_key: str) -> None:
        table = self._tables.simulation_definition
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, definition.organization_id)
            existing = session.execute(
                select(table.c.status).where(
                    table.c.simulation_id == definition.simulation_id,
                    table.c.version == definition.version,
                    table.c.organization_id == definition.organization_id,
                )
            ).first()
            if existing is not None and existing.status == DefinitionStatus.PUBLISHED.value:
                raise ImmutableDefinitionError(definition.simulation_id, definition.version)
            if existing is None:
                session.execute(
                    insert(table).values(
                        simulation_id=definition.simulation_id,
                        version=definition.version,
                        organization_id=definition.organization_id,
                        document=definition.to_document(),
                        content_hash=definition.content_hash(),
                        status=definition.status.value,
                        scoring_key=scoring_key,
                        created_at=_now(),
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.simulation_id == definition.simulation_id,
                        table.c.version == definition.version,
                        table.c.organization_id == definition.organization_id,
                    )
                    .values(
                        document=definition.to_document(),
                        content_hash=definition.content_hash(),
                        status=definition.status.value,
                        scoring_key=scoring_key,
                    )
                )
            uow.commit()

    def get_definition(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> SimulationDefinition | None:
        table = self._tables.simulation_definition
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.simulation_id == simulation_id,
                    table.c.version == version,
                    table.c.organization_id == organization_id,
                )
            ).first()
        return _definition_from_row(row) if row is not None else None

    def publish_definition(self, definition: SimulationDefinition) -> None:
        table = self._tables.simulation_definition
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, definition.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.simulation_id == definition.simulation_id,
                    table.c.version == definition.version,
                    table.c.organization_id == definition.organization_id,
                )
                .values(status=definition.status.value)
            )
            uow.commit()

    def get_scoring_key(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> str | None:
        table = self._tables.simulation_definition
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.scoring_key).where(
                    table.c.simulation_id == simulation_id,
                    table.c.version == version,
                    table.c.organization_id == organization_id,
                )
            ).first()
        return None if row is None else str(row.scoring_key)

    def set_trust_tier(self, tier: TrustTier) -> None:
        table = self._tables.trust_tier
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, tier.organization_id)
            existing = session.execute(
                select(table.c.tier).where(
                    table.c.organization_id == tier.organization_id,
                    table.c.tier == tier.tier.value,
                )
            ).first()
            values = {"approved": tier.approved, "max_quota": tier.max_quota.to_dict()}
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=tier.organization_id,
                        tier=tier.tier.value,
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == tier.organization_id,
                        table.c.tier == tier.tier.value,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_trust_tier(self, *, organization_id: str, tier: str) -> TrustTier | None:
        table = self._tables.trust_tier
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.tier == tier,
                )
            ).first()
        if row is None:
            return None
        return TrustTier(
            organization_id=row.organization_id,
            tier=RuntimeTier(row.tier),
            approved=bool(row.approved),
            max_quota=ResourceQuota.from_dict(row.max_quota),
        )

    def add_score(self, score: Score) -> None:
        table = self._tables.score
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, score.organization_id)
            uow.session.execute(
                insert(table).values(
                    score_id=score.score_id,
                    organization_id=score.organization_id,
                    run_id=score.run_id,
                    profile_id=score.profile_id,
                    profile_version=score.profile_version,
                    seed=score.seed,
                    value=score.value,
                    breakdown=[list(pair) for pair in score.breakdown],
                    created_at=_now(),
                )
            )
            uow.commit()

    def get_score(self, *, organization_id: str, run_id: str) -> Score | None:
        table = self._tables.score
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.run_id == run_id,
                )
            ).first()
        if row is None:
            return None
        return Score(
            score_id=row.score_id,
            run_id=row.run_id,
            organization_id=row.organization_id,
            profile_id=row.profile_id,
            profile_version=row.profile_version,
            seed=row.seed,
            value=float(row.value),
            breakdown=tuple((str(k), float(v)) for k, v in row.breakdown),
        )


class SqlAlchemyEvidenceStore:
    """PostgreSQL hash-chained evidence store; tenant-scoped, RLS GUC set per transaction."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: SimulationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def record(self, evidence: RunEvidence) -> None:
        table = self._tables.run_evidence
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, evidence.organization_id)
            uow.session.execute(
                insert(table).values(
                    run_id=evidence.run_id,
                    organization_id=evidence.organization_id,
                    simulation_id=evidence.simulation_id,
                    definition_hash=evidence.definition_hash,
                    runtime_version=evidence.runtime_version,
                    inputs_hash=evidence.inputs_hash,
                    entries=[_entry_to_dict(e) for e in evidence.entries],
                    outcome=evidence.outcome,
                    head_hash=evidence.head_hash,
                    created_at=_now(),
                )
            )
            uow.commit()

    def get(self, *, organization_id: str, run_id: str) -> RunEvidence | None:
        table = self._tables.run_evidence
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.run_id == run_id,
                )
            ).first()
        if row is None:
            return None
        entries = tuple(_entry_from_dict(d) for d in row.entries)
        return RunEvidence(
            run_id=row.run_id,
            organization_id=row.organization_id,
            simulation_id=row.simulation_id,
            definition_hash=row.definition_hash,
            runtime_version=row.runtime_version,
            inputs_hash=row.inputs_hash,
            entries=entries,
            outcome=row.outcome,
        )


def _entry_to_dict(entry: EvidenceEntry) -> dict[str, object]:
    return {
        "seq": entry.seq,
        "kind": entry.kind,
        "payload": dict(entry.payload),
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }


def _entry_from_dict(data: dict[str, Any]) -> EvidenceEntry:
    return EvidenceEntry(
        seq=int(data["seq"]),
        kind=str(data["kind"]),
        payload=dict(data["payload"]),
        prev_hash=str(data["prev_hash"]),
        entry_hash=str(data["entry_hash"]),
    )
