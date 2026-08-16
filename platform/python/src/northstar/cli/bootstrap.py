"""One-touch bootstrap orchestration for ``--profile ci`` (FR-DX-001/002, NFR-OPS-001).

Implements the deterministic, non-interactive slice of the one-touch bootstrap contract
(``spec/reference/bootstrap-contract.md``) that a clean checkout can prove end-to-end against a
live PostgreSQL: a small **state machine** that runs

    doctor (preflight) -> migrate (alembic upgrade head) -> seed (deterministic) -> smoke journey

Each step returns a structured :class:`StepResult`; a failed/blocked step halts the machine, leaves
a diagnostic + recovery hint, and marks the remaining steps ``not_run`` (NOT RUN != PASS). Every
step is **idempotent**, so re-running bootstrap is safe and a second run still reports ``pass``
without duplicating seed rows. This module owns orchestration only — it reuses the existing
adapters (Alembic config, SQLAlchemy engine/UoW/outbox), kernel buses, policy, audit and the
deterministic seed; it does not re-implement them.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from northstar.adapters.persistence_sqlalchemy.engine import (
    create_engine_from_url,
    create_session_factory,
    resolve_database_url,
)
from northstar.adapters.persistence_sqlalchemy.outbox import SqlAlchemyOutbox
from northstar.adapters.persistence_sqlalchemy.runtime_tables import RUNTIME_TABLES
from northstar.adapters.persistence_sqlalchemy.seed import apply_seed
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.audit import AuditOutcome, InMemoryAuditRecorder
from northstar.kernel.capabilities import CapabilityDispatcher, CapabilityRegistry
from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.events import DomainEvent
from northstar.kernel.messaging import Command, CommandBus, CommandInvocation
from northstar.kernel.policy import InMemoryPolicyEvaluator, PolicyGrant

# Terminal step statuses (mirror cli-output result vocabulary minus "warn").
PASS = "pass"  # noqa: S105 - a cli-output status literal, not a secret
FAIL = "fail"
BLOCKED = "blocked"
NOT_RUN = "not_run"

_SMOKE_CAPABILITY = "sample.notes.record"
_SMOKE_VERSION = "1.0.0"
_SMOKE_ACTOR = "bootstrap-smoke"
_SMOKE_EVENT_TYPE = "northstar.sample.note-recorded.v1"


@dataclass
class StepResult:
    """The structured outcome of one bootstrap step."""

    name: str
    status: str  # pass | fail | blocked | not_run
    detail: str
    recovery: str | None = None
    changed: bool = True


class BootstrapStepError(Exception):
    """Raised by a step to signal a controlled fail/blocked outcome with a recovery hint."""

    def __init__(self, status: str, detail: str, *, recovery: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.recovery = recovery


@dataclass
class BootstrapContext:
    """Mutable orchestration context threaded through the steps."""

    profile: str
    database_url: str | None = None
    alembic_version_schema: str | None = None
    resolved_url: str | None = None


StepFn = Callable[[BootstrapContext], StepResult]


@dataclass
class BootstrapReport:
    """The full ordered list of step results plus the derived overall status."""

    results: list[StepResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(r.status == FAIL for r in self.results):
            return FAIL
        if any(r.status in (BLOCKED, NOT_RUN) for r in self.results):
            return BLOCKED
        return PASS

    @property
    def ok(self) -> bool:
        return self.status == PASS


def execute_steps(steps: Sequence[tuple[str, StepFn]], ctx: BootstrapContext) -> list[StepResult]:
    """Run ``steps`` in order, halting after the first non-``pass`` step.

    Steps after a halt are recorded as ``not_run`` so the machine's sequencing is always visible
    (a downstream step is never silently skipped). A :class:`BootstrapStepError` becomes its
    declared fail/blocked result; any other exception is a controlled ``fail`` (never a crash).
    """
    results: list[StepResult] = []
    halted = False
    for name, fn in steps:
        if halted:
            results.append(
                StepResult(name, NOT_RUN, "not run: a preceding step did not pass", changed=False)
            )
            continue
        try:
            result = fn(ctx)
        except BootstrapStepError as exc:
            result = StepResult(name, exc.status, exc.detail, recovery=exc.recovery, changed=False)
        except Exception as exc:  # a step must never crash the orchestrator
            result = StepResult(
                name,
                FAIL,
                f"unexpected error: {exc.__class__.__name__}: {exc}",
                recovery="inspect logs and re-run `northstar bootstrap --profile ci`",
                changed=False,
            )
        results.append(result)
        if result.status != PASS:
            halted = True
    return results


# ---- concrete CI steps -----------------------------------------------------------------


def doctor_step(ctx: BootstrapContext) -> StepResult:
    """Preflight: supported runtime + a resolvable, reachable ``DATABASE_URL``."""
    py_ok = sys.version_info[:2] >= (3, 13)
    if not py_ok:
        raise BootstrapStepError(
            FAIL,
            f"python {sys.version.split()[0]} is unsupported (need >= 3.13)",
            recovery="install Python 3.13+ (see .tool-versions) and re-run",
        )
    try:
        url = resolve_database_url(ctx.database_url)
    except Exception:
        raise BootstrapStepError(
            BLOCKED,
            "DATABASE_URL is not set; cannot reach the database",
            recovery=(
                "set DATABASE_URL to your PostgreSQL connection string "
                "(postgresql+psycopg scheme), then re-run"
            ),
        ) from None
    try:
        engine = create_engine_from_url(url)
        try:
            with engine.connect() as conn:
                from sqlalchemy import text

                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except Exception as exc:
        raise BootstrapStepError(
            BLOCKED,
            f"database dependency is unavailable: {exc.__class__.__name__}",
            recovery="start PostgreSQL (e.g. `northstar up`) then re-run bootstrap",
        ) from None
    ctx.resolved_url = url
    return StepResult(
        "doctor", PASS, "runtime >= 3.13 and database dependency reachable", changed=False
    )


def migrate_step(ctx: BootstrapContext) -> StepResult:
    """Apply framework/module migrations in order (``alembic upgrade head``); idempotent."""
    from alembic import command
    from alembic.script import ScriptDirectory

    from northstar.adapters.persistence_sqlalchemy.migrations.config import make_alembic_config

    url = ctx.resolved_url or resolve_database_url(ctx.database_url)
    config = make_alembic_config(url)
    if ctx.alembic_version_schema:
        config.set_main_option("version_table_schema", ctx.alembic_version_schema)
    head = ScriptDirectory.from_config(config).get_current_head()
    try:
        # Alembic may emit progress on stdout; keep the CLI's JSON stdout clean.
        with redirect_stdout(sys.stderr):
            command.upgrade(config, "head")
    except Exception as exc:
        raise BootstrapStepError(
            FAIL,
            f"alembic upgrade failed: {exc.__class__.__name__}: {exc}",
            recovery="inspect the migration error, fix the database, then re-run bootstrap",
        ) from None
    return StepResult("migrate", PASS, f"database at migration head {head}")


def seed_step(ctx: BootstrapContext) -> StepResult:
    """Insert deterministic reference registry rows (idempotent, no duplicates on re-run)."""
    url = ctx.resolved_url or resolve_database_url(ctx.database_url)
    engine = create_engine_from_url(url)
    try:
        outcome = apply_seed(engine)
    except Exception as exc:
        raise BootstrapStepError(
            FAIL,
            f"seed failed: {exc.__class__.__name__}: {exc}",
            recovery="verify migrations applied (registry tables exist), then re-run",
        ) from None
    finally:
        engine.dispose()
    return StepResult(
        "seed",
        PASS,
        (f"reference seed applied: inserted {outcome.inserted} row(s), {outcome.total} present"),
        changed=outcome.inserted > 0,
    )


@dataclass
class SmokeOutcome:
    """Structured result of the in-process smoke journey."""

    ok: bool
    detail: str
    event_id: str | None = None


class _SmokeNoteHandler:
    """A sample write capability that commits a domain event to the transactional outbox."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def handle(self, request: CommandInvocation) -> dict[str, Any]:
        payload = request.payload
        event = DomainEvent(
            event_id=str(uuid.uuid4()),
            event_type=_SMOKE_EVENT_TYPE,
            source="module://bootstrap-smoke",
            correlation_id=request.context.correlation_id,
            actor=request.context.actor,
            aggregate_type="sample-note",
            aggregate_id=payload["note_id"],
            occurred_at=datetime.now(UTC),
            data={"note_id": payload["note_id"], "text": payload["text"]},
            dataschema="schema://events/sample-note-recorded/1",
        )
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            SqlAlchemyOutbox(uow.session).append(event)
            uow.commit()
        return {"note_id": payload["note_id"], "event_id": event.event_id}


def run_smoke_journey(database_url: str) -> SmokeOutcome:
    """Exercise the kernel vertical slice in-process against the live database.

    Readiness first, then a sample command through the real command bus: deny-by-default policy
    (allowed via an explicit grant) -> handler -> tamper-evident audit record -> domain event
    committed to the transactional outbox. Returns a structural pass/fail (no exception leaks).
    """
    from sqlalchemy import func, select, text

    engine = create_engine_from_url(database_url)
    try:
        # Readiness: the database dependency answers.
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        session_factory = create_session_factory(engine)
        registry = CapabilityRegistry()
        registry.register(_SMOKE_CAPABILITY, _SMOKE_VERSION, _SmokeNoteHandler(session_factory))
        dispatcher = CapabilityDispatcher(registry)
        policy = InMemoryPolicyEvaluator(
            grants=(PolicyGrant(action=_SMOKE_CAPABILITY, actor_ids=frozenset({_SMOKE_ACTOR})),)
        )
        audit = InMemoryAuditRecorder()
        bus = CommandBus(dispatcher, policy, audit)

        note_id = f"smoke-{uuid.uuid4().hex[:12]}"
        context = RequestContext(
            actor=Actor(type=ActorType.OPERATOR, id=_SMOKE_ACTOR),
            correlation_id=str(uuid.uuid4()),
        )
        command = Command(
            capability=_SMOKE_CAPABILITY,
            version=_SMOKE_VERSION,
            payload={"note_id": note_id, "text": "bootstrap smoke journey"},
        )
        result = bus.dispatch(command, context)

        audit_ok = len(audit.records) == 1 and audit.records[0].outcome is AuditOutcome.SUCCESS
        table = RUNTIME_TABLES.outbox_event
        with session_factory() as session:
            outbox_count = int(
                session.execute(
                    select(func.count()).select_from(table).where(table.c.aggregate_id == note_id)
                ).scalar_one()
            )
        event_id = str(result.value["event_id"])
        ok = bool(audit_ok and outbox_count == 1)
        if ok:
            detail = "readiness ok; command wrote 1 audit record + 1 outbox event"
        else:
            detail = (
                f"smoke journey structural check failed "
                f"(audit_ok={audit_ok}, outbox_count={outbox_count})"
            )
        return SmokeOutcome(ok=ok, detail=detail, event_id=event_id)
    except Exception as exc:
        return SmokeOutcome(
            ok=False, detail=f"smoke journey error: {exc.__class__.__name__}: {exc}"
        )
    finally:
        engine.dispose()


def smoke_step(ctx: BootstrapContext) -> StepResult:
    """Run the in-process smoke journey and translate it to a step result."""
    url = ctx.resolved_url or resolve_database_url(ctx.database_url)
    outcome = run_smoke_journey(url)
    if not outcome.ok:
        raise BootstrapStepError(
            FAIL,
            outcome.detail,
            recovery="check the database/runtime health, then re-run bootstrap",
        )
    return StepResult("smoke", PASS, outcome.detail, changed=True)


def ci_steps() -> list[tuple[str, StepFn]]:
    """The ordered CI bootstrap state machine (doctor -> migrate -> seed -> smoke)."""
    return [
        ("doctor", doctor_step),
        ("migrate", migrate_step),
        ("seed", seed_step),
        ("smoke", smoke_step),
    ]


def run_bootstrap(
    profile: str,
    *,
    database_url: str | None = None,
    alembic_version_schema: str | None = None,
    steps: Sequence[tuple[str, StepFn]] | None = None,
) -> BootstrapReport:
    """Run the CI bootstrap state machine and return the structured report.

    ``steps`` is injectable so unit tests can drive sequencing/idempotency with staged steps;
    ``alembic_version_schema`` isolates Alembic's bookkeeping table (integration tests run it in a
    throwaway schema so shared state is never clobbered).
    """
    ctx = BootstrapContext(
        profile=profile,
        database_url=database_url,
        alembic_version_schema=alembic_version_schema,
    )
    report = BootstrapReport()
    report.results = execute_steps(steps if steps is not None else ci_steps(), ctx)
    return report
