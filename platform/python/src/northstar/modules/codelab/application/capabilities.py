"""Codelab capabilities: one authoritative implementation per action (LAW-04).

``codelab.run.execute`` (command) validates the submission, runs it behind the
:class:`CodeSandboxPort`, and records an IMMUTABLE, tracked :class:`CodeRun` through the
:class:`CodeRunStorePort` — so every execution the learner attempts is durably tracked AND audited by
the kernel command bus. ``codelab.run.list`` (query) returns the caller's own tracked runs. Tenant
scope and the acting subject come from the authenticated :class:`RequestContext`, never the payload
(rule 50).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import TenantScopeMissing
from ..domain.model import CodeLimits, CodeRun, validate_submission
from .ports import CodeRunStorePort, CodeSandboxPort

CAP_VERSION = "1.0.0"

CAP_RUN = "codelab.run.execute"
CAP_LIST_RUNS = "codelab.run.list"

CODELAB_CAPABILITIES: tuple[str, ...] = (CAP_RUN, CAP_LIST_RUNS)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class RunCodeCommand:
    code: str
    language: str = "python"
    lesson_id: str | None = None
    block_id: str | None = None
    stdin: str = ""


@dataclass(frozen=True, slots=True)
class RunResultView:
    run_id: str
    language: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    truncated: bool
    outcome: str
    record_sha256: str
    created_at: str
    lesson_id: str | None
    block_id: str | None


@dataclass(frozen=True, slots=True)
class ListRunsQuery:
    limit: int = 50


@dataclass(frozen=True, slots=True)
class RunListView:
    runs: tuple[RunResultView, ...]


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    scope = getattr(getattr(invocation, "context", None), "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


def _actor_id(invocation: object) -> str:
    actor = getattr(getattr(invocation, "context", None), "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return subject


class RunCode:
    """``codelab.run.execute`` — sandbox-execute a submission and record the tracked run."""

    def __init__(
        self,
        *,
        sandbox: CodeSandboxPort,
        store: CodeRunStorePort,
        clock: Clock,
        id_factory: IdFactory,
        limits: CodeLimits | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._store = store
        self._clock = clock
        self._id_factory = id_factory
        self._limits = limits or CodeLimits()

    def handle(self, request: object) -> RunResultView:
        command = _typed(request, RunCodeCommand)
        organization_id = _tenant(request)
        actor_id = _actor_id(request)
        validate_submission(language=command.language, code=command.code)

        result = self._sandbox.run(
            language=command.language,
            code=command.code,
            stdin=command.stdin,
            limits=self._limits,
        )
        run = CodeRun(
            run_id=self._id_factory(),
            organization_id=organization_id,
            actor_id=actor_id,
            language=command.language,
            code=command.code,
            lesson_id=command.lesson_id,
            block_id=command.block_id,
            result=result,
            created_at=self._clock(),
        ).with_hash()
        self._store.record(run)
        return _to_view(run)


class ListRuns:
    """``codelab.run.list`` (query) — the caller's own tracked runs, newest first."""

    def __init__(self, *, store: CodeRunStorePort) -> None:
        self._store = store

    def handle(self, request: object) -> RunListView:
        query = _typed(request, ListRunsQuery)
        organization_id = _tenant(request)
        actor_id = _actor_id(request)
        limit = max(1, min(query.limit, 200))
        runs = self._store.list_for_actor(
            organization_id=organization_id, actor_id=actor_id, limit=limit
        )
        return RunListView(runs=tuple(_to_view(r) for r in runs))


def _to_view(run: CodeRun) -> RunResultView:
    return RunResultView(
        run_id=run.run_id,
        language=run.language,
        stdout=run.result.stdout,
        stderr=run.result.stderr,
        exit_code=run.result.exit_code,
        duration_ms=run.result.duration_ms,
        timed_out=run.result.timed_out,
        truncated=run.result.truncated,
        outcome=run.result.outcome,
        record_sha256=run.record_sha256,
        created_at=run.created_at.isoformat(),
        lesson_id=run.lesson_id,
        block_id=run.block_id,
    )
