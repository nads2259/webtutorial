"""In-process graceful-drain state machine + migration-compatibility gate (EVAL-OPS-002).

Pure, infra-free logic (LAW-12 keeps the semantics testable without an orchestrator; the packet's
non-scope confirms drain is proven in-process). On a drain signal the process stops admitting NEW
work — further admissions get a ``503`` + ``Retry-After`` (again-later) — while ALREADY in-flight
requests are allowed to run to completion; the process reports ``DRAINED`` only once the in-flight
count reaches zero, so no acknowledged work is lost (docs/18 §5). Before a rolling cutover the
deploy must pass the migration-compatibility gate (expand/migrate/contract, docs/18 §9): a new
artifact is refused if the deployed schema does not satisfy the artifact's minimum required
version.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_RETRY_AFTER_SECONDS = 5


class DrainState(StrEnum):
    """Lifecycle of a drainable process."""

    RUNNING = "running"
    DRAINING = "draining"
    DRAINED = "drained"


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Whether a newly-arriving request may be admitted, with HTTP-shaped drain semantics."""

    admitted: bool
    status_code: int
    reason: str
    retry_after_seconds: int | None = None


class DrainRejectedError(RuntimeError):
    """Raised when a caller enters the request scope while the process is draining."""

    def __init__(self, decision: AdmissionDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class DrainController:
    """Admission controller that drains in-flight work gracefully on a shutdown signal.

    Single-threaded/synchronous by design (the reference process model): callers admit a request,
    do their work, then complete it. Concurrency in a real deployment is provided by the ASGI
    server; this controller owns only the admission/drain decision so it stays pure and testable.
    """

    def __init__(self, *, retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS) -> None:
        if retry_after_seconds <= 0:
            raise ValueError("retry_after_seconds must be positive")
        self._retry_after_seconds = retry_after_seconds
        self._state = DrainState.RUNNING
        self._in_flight = 0

    @property
    def state(self) -> DrainState:
        return self._state

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def admit(self) -> AdmissionDecision:
        """Decide whether to admit a new request; increments the in-flight count when admitted."""
        if self._state is DrainState.RUNNING:
            self._in_flight += 1
            return AdmissionDecision(admitted=True, status_code=200, reason="admitted")
        return AdmissionDecision(
            admitted=False,
            status_code=503,
            reason="server is draining; retry after the indicated delay",
            retry_after_seconds=self._retry_after_seconds,
        )

    def complete(self) -> None:
        """Mark one in-flight request finished; transition to DRAINED when the last one drains."""
        if self._in_flight <= 0:
            raise RuntimeError("complete() called with no in-flight request")
        self._in_flight -= 1
        if self._state is DrainState.DRAINING and self._in_flight == 0:
            self._state = DrainState.DRAINED

    def begin_drain(self) -> DrainState:
        """Signal drain: stop admitting new work; drain immediately if nothing is in flight."""
        if self._state is DrainState.RUNNING:
            self._state = DrainState.DRAINING if self._in_flight > 0 else DrainState.DRAINED
        return self._state

    @contextmanager
    def request(self) -> Iterator[None]:
        """Scope one in-flight request; raise :class:`DrainRejectedError` if admission refused."""
        decision = self.admit()
        if not decision.admitted:
            raise DrainRejectedError(decision)
        try:
            yield
        finally:
            self.complete()


def _parse_version(value: str) -> tuple[int, ...]:
    parts = value.strip().lstrip("vV").split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"unparseable schema version: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class MigrationCompatibility:
    """Result of the pre-cutover migration-compatibility gate (docs/18 §9)."""

    deployed_schema_version: str
    artifact_required_version: str
    compatible: bool
    reason: str


def check_migration_compatibility(
    *, deployed_schema_version: str, artifact_required_version: str
) -> MigrationCompatibility:
    """Refuse a cutover whose deployed schema is older than the artifact's minimum requirement.

    Expand/migrate/contract compatibility means a new release must never run against a schema it
    was not built for. The gate is deny-by-default: an unparseable or older deployed schema blocks
    the cutover with an explicit reason rather than proceeding optimistically.
    """
    deployed = _parse_version(deployed_schema_version)
    required = _parse_version(artifact_required_version)
    compatible = deployed >= required
    reason = (
        "deployed schema satisfies the artifact's minimum required version"
        if compatible
        else "deployed schema is older than the artifact requires; cutover blocked"
    )
    return MigrationCompatibility(
        deployed_schema_version=deployed_schema_version,
        artifact_required_version=artifact_required_version,
        compatible=compatible,
        reason=reason,
    )
