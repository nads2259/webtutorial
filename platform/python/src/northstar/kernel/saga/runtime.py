"""The pure saga coordinator: ordered steps, reverse-order compensation, idempotent per saga id.

FR-KRN-004 / EVAL-KRN-004. A :class:`SagaCoordinator` executes an ordered list of :class:`SagaStep`
against a shared, caller-provided mutable ``context``. Each step is an ``action`` paired with a
``compensation``:

* on success every action runs in order and the saga COMMITs;
* if an action raises, the coordinator runs the compensations for the steps that already applied,
  in REVERSE order, then reports COMPENSATED — the failing step (whose action raised) is treated as
  not-applied and is never compensated, so a partially-applied operation leaves no partial effect;
* the terminal outcome is recorded through the :class:`SagaStateStorePort`, so re-executing the same
  ``(organization_id, saga_id)`` is idempotent: the recorded outcome is returned WITHOUT re-running
  any action or compensation. Given the same steps + context the coordinator is deterministic.

Stdlib-only (rule 10): actions/compensations are plain callables injected by the caller; the kernel
owns orchestration + compensation ordering, never the side effects themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .ports import InMemorySagaStateStore, SagaRecord, SagaStateStorePort, SagaStatus

SagaContext = dict[str, Any]
StepAction = Callable[[SagaContext], None]


@dataclass(frozen=True, slots=True)
class SagaStep:
    """One saga step: a named ``action`` and its ``compensation`` (both act on the saga context).

    The ``compensation`` MUST undo the effect of ``action`` and is only ever invoked for a step
    whose action completed without raising. Steps within one saga MUST have unique ``name`` so the
    recorded/compensated step lists are unambiguous.
    """

    name: str
    action: StepAction
    compensation: StepAction


@dataclass(frozen=True, slots=True)
class SagaOutcome:
    """The result of executing a saga: terminal status and the applied/compensated step names."""

    saga_id: str
    status: SagaStatus
    completed_steps: tuple[str, ...]
    compensated_steps: tuple[str, ...]
    error: str | None = None

    @property
    def committed(self) -> bool:
        return self.status is SagaStatus.COMMITTED


class SagaError(Exception):
    """Raised for a malformed saga definition (e.g. duplicate step names)."""


class SagaCoordinator:
    """Deterministic, idempotent saga executor over an injected :class:`SagaStateStorePort`."""

    def __init__(self, store: SagaStateStorePort | None = None) -> None:
        self._store = store if store is not None else InMemorySagaStateStore()

    def execute(
        self,
        *,
        organization_id: str,
        saga_id: str,
        steps: Sequence[SagaStep],
        context: SagaContext | None = None,
    ) -> SagaOutcome:
        """Execute ``steps`` for ``saga_id``; idempotent per ``(organization_id, saga_id)``."""
        names = [step.name for step in steps]
        if len(names) != len(set(names)):
            raise SagaError(f"saga '{saga_id}' has duplicate step names: {names}")

        existing = self._store.get(organization_id=organization_id, saga_id=saga_id)
        if existing is not None:
            # Already terminal — replay is a no-op returning the recorded outcome (idempotent).
            return _outcome_from_record(existing)

        ctx: SagaContext = context if context is not None else {}
        applied: list[SagaStep] = []
        for step in steps:
            try:
                step.action(ctx)
            except Exception as err:  # a step failed — compensate applied steps in reverse
                compensated = self._compensate(applied, ctx)
                outcome = SagaOutcome(
                    saga_id=saga_id,
                    status=SagaStatus.COMPENSATED,
                    completed_steps=tuple(s.name for s in applied),
                    compensated_steps=compensated,
                    error=f"{type(err).__name__}: {err}",
                )
                self._record(organization_id, outcome)
                return outcome
            applied.append(step)

        outcome = SagaOutcome(
            saga_id=saga_id,
            status=SagaStatus.COMMITTED,
            completed_steps=tuple(s.name for s in applied),
            compensated_steps=(),
            error=None,
        )
        self._record(organization_id, outcome)
        return outcome

    @staticmethod
    def _compensate(applied: Sequence[SagaStep], ctx: SagaContext) -> tuple[str, ...]:
        compensated: list[str] = []
        for step in reversed(applied):
            step.compensation(ctx)
            compensated.append(step.name)
        return tuple(compensated)

    def _record(self, organization_id: str, outcome: SagaOutcome) -> None:
        self._store.put(
            SagaRecord(
                organization_id=organization_id,
                saga_id=outcome.saga_id,
                status=outcome.status,
                completed_steps=outcome.completed_steps,
                compensated_steps=outcome.compensated_steps,
                error=outcome.error,
            )
        )


def _outcome_from_record(record: SagaRecord) -> SagaOutcome:
    return SagaOutcome(
        saga_id=record.saga_id,
        status=record.status,
        completed_steps=record.completed_steps,
        compensated_steps=record.compensated_steps,
        error=record.error,
    )


__all__ = [
    "SagaContext",
    "SagaCoordinator",
    "SagaError",
    "SagaOutcome",
    "SagaStep",
]
