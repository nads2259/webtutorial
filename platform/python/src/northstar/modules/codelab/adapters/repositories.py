"""Codelab run stores (in-memory + SQLAlchemy) implementing :class:`CodeRunStorePort`.

Every SQLAlchemy read/write is filtered by ``organization_id`` (rule 50, tenant isolation) and sets
the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth (FR-POL-004). The store
is append-only: a run is recorded once and never mutated (tamper-evident action log).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import CodeRun, ExecResult
from .tables import CodelabTables


class InMemoryCodeRunStore:
    """In-memory store for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._runs: list[CodeRun] = []

    def record(self, run: CodeRun) -> None:
        self._runs.append(run)

    def list_for_actor(
        self, *, organization_id: str, actor_id: str, limit: int = 50
    ) -> Sequence[CodeRun]:
        rows = [
            r
            for r in self._runs
            if r.organization_id == organization_id and r.actor_id == actor_id
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]


class SqlAlchemyCodeRunStore:
    """PostgreSQL store; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: CodelabTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def record(self, run: CodeRun) -> None:
        table = self._tables.code_run
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, run.organization_id)
            uow.session.execute(
                insert(table).values(
                    run_id=run.run_id,
                    organization_id=run.organization_id,
                    actor_id=run.actor_id,
                    language=run.language,
                    code=run.code,
                    lesson_id=run.lesson_id,
                    block_id=run.block_id,
                    stdout=run.result.stdout,
                    stderr=run.result.stderr,
                    exit_code=run.result.exit_code,
                    duration_ms=run.result.duration_ms,
                    timed_out=run.result.timed_out,
                    truncated=run.result.truncated,
                    outcome=run.result.outcome,
                    record_sha256=run.record_sha256,
                    created_at=run.created_at,
                )
            )
            uow.commit()

    def list_for_actor(
        self, *, organization_id: str, actor_id: str, limit: int = 50
    ) -> Sequence[CodeRun]:
        table = self._tables.code_run
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.actor_id == actor_id,
                )
                .order_by(table.c.created_at.desc())
                .limit(limit)
            ).all()
        return [_row_to_run(r) for r in rows]


def _row_to_run(r: object) -> CodeRun:
    return CodeRun(
        run_id=r.run_id,
        organization_id=r.organization_id,
        actor_id=r.actor_id,
        language=r.language,
        code=r.code,
        lesson_id=r.lesson_id,
        block_id=r.block_id,
        result=ExecResult(
            stdout=r.stdout,
            stderr=r.stderr,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
            timed_out=r.timed_out,
            truncated=r.truncated,
        ),
        created_at=_aware(r.created_at),
        record_sha256=r.record_sha256,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
