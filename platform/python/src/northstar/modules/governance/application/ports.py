"""Ports (abstractions) for the governance application layer (rule 10/20, DIP).

The repository is tenant-aware: every read/write is scoped by ``organization_id`` so a caller can
never reach another tenant's decision records or control exceptions (rule 50). It exposes the
governance surface as queries/writes — no consumer ever touches the tables directly (FR-GOV-003).
The approver-directory port answers deny-by-default authorization: only an authorized approver may
grant or revoke a control exception.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.model import ControlException, DecisionRecord


@runtime_checkable
class GovernanceRepositoryPort(Protocol):
    """Persists and reads governance decision records + control exceptions, tenant-scoped.

    Decision records are append-only (immutability is enforced in the domain — there is no update
    method for a decision; a correction is a new superseding record).
    """

    def add_decision(self, decision: DecisionRecord) -> None: ...

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord | None:
        """Return the decision only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def list_decisions(self, *, organization_id: str) -> Sequence[DecisionRecord]:
        """Return the tenant's decision records (the decision trace, EVAL-GOV-001)."""
        ...

    def add_exception(self, exception: ControlException) -> None: ...

    def get_exception(self, *, organization_id: str, exception_id: str) -> ControlException | None:
        """Return the exception only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def update_exception(self, exception: ControlException) -> None:
        """Persist a revocation (tenant-scoped); a decision record is never updated in place."""
        ...

    def list_exceptions_for_control(
        self, *, organization_id: str, control: str
    ) -> Sequence[ControlException]:
        """Return every exception scoped to ``control`` for the tenant (for the gate evaluation)."""
        ...


@runtime_checkable
class ApproverDirectoryPort(Protocol):
    """Answers whether an actor is an authorized exception approver in a tenant (deny-default)."""

    def is_approver(self, *, organization_id: str, actor_id: str) -> bool: ...
