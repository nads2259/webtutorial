"""Pure multi-scope AI cost/budget value objects and decisions (docs/10, FR-AI-008).

Infrastructure-free (rule 10, LAW-02). These frozen value objects and pure functions encode
governed cost control for a scoped AI actor across MULTIPLE scopes — per-actor, per-tenant and
per-workflow/campaign (the per-run tool-call budget lives in the Tool Broker). A request whose
projected spend exceeds ANY applicable budget is denied (:func:`evaluate_budget`), and the
provider cost recorded per interaction is reconciled against the ledger total
(:func:`reconcile_cost`).

Nothing here reaches a database or a provider SDK; the ledger persistence is a port
(:class:`northstar.modules.ai.application.ports.BudgetLedgerPort`). These are the deterministic
basis the ``EVAL-AI-008`` cost-enforcement defenses rest on (LLM10 unbounded-consumption).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .errors import AiInvariantViolation


class BudgetScope(StrEnum):
    """The scopes an AI cost budget applies at (docs/10 §12, FR-AI-008).

    ``RUN`` is the per-run tool-call budget enforced by the Tool Broker; ``ACTOR``, ``TENANT`` and
    ``WORKFLOW`` are the multi-scope cost budgets enforced by the budget guard.
    """

    RUN = "run"
    ACTOR = "actor"
    TENANT = "tenant"
    WORKFLOW = "workflow"


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    """A configured spend ceiling (in cost units) for one ``scope``/``scope_id`` over a window."""

    scope: BudgetScope
    scope_id: str
    limit_units: float
    window: str = "monthly"

    def __post_init__(self) -> None:
        if not self.scope_id:
            raise AiInvariantViolation(
                "budget limit requires a scope_id", code="ai.budget.scope_id"
            )
        if self.limit_units < 0:
            raise AiInvariantViolation(
                "budget limit_units must be non-negative", code="ai.budget.limit"
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.scope.value, self.scope_id)


@dataclass(frozen=True, slots=True)
class CostEntry:
    """A recorded provider cost for one AI interaction, attributed to its scopes (FR-AI-009).

    ``cost_units`` is the internal accounting figure the ledger reconciles; ``provider_cost`` is the
    provider-reported charge for the same interaction (used by :func:`reconcile_cost`).
    """

    entry_id: str
    organization_id: str
    actor_id: str
    workflow_id: str | None
    cost_units: float
    provider_cost: float
    provider: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.entry_id or not self.organization_id or not self.actor_id:
            raise AiInvariantViolation(
                "cost entry requires entry_id, organization_id and actor_id",
                code="ai.cost.identity",
            )
        if self.cost_units < 0 or self.provider_cost < 0:
            raise AiInvariantViolation(
                "cost entry amounts must be non-negative", code="ai.cost.amount"
            )


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """The outcome of a multi-scope budget check (deny-by-default naming the first breach)."""

    allowed: bool
    exceeded_scope: BudgetScope | None = None
    exceeded_scope_id: str | None = None
    exceeded_limit: float | None = None
    projected_units: float | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """The reconciliation of recorded ledger spend against the provider-reported total."""

    scope: BudgetScope
    scope_id: str
    ledger_units: float
    provider_reported: float
    delta: float
    within_tolerance: bool


def evaluate_budget(
    *,
    limits: Sequence[BudgetLimit],
    spent: Mapping[tuple[str, str], float],
    requested_cost: float,
) -> BudgetDecision:
    """Deny if the projected spend would exceed ANY applicable budget (FR-AI-008).

    For each applicable ``limit`` the projected spend is the already-recorded spend for that
    ``scope``/``scope_id`` plus ``requested_cost``; the FIRST limit that would be breached denies
    the whole request (a request exceeding any one scope is rejected). Limits are evaluated in order
    given, so callers pass them most-specific-first for a stable, explainable breach.
    """
    if requested_cost < 0:
        raise AiInvariantViolation("requested_cost must be non-negative", code="ai.budget.request")
    for limit in limits:
        prior = spent.get(limit.key, 0.0)
        projected = prior + requested_cost
        if projected > limit.limit_units:
            return BudgetDecision(
                allowed=False,
                exceeded_scope=limit.scope,
                exceeded_scope_id=limit.scope_id,
                exceeded_limit=limit.limit_units,
                projected_units=projected,
            )
    return BudgetDecision(allowed=True)


def reconcile_cost(
    *,
    scope: BudgetScope,
    scope_id: str,
    ledger_units: float,
    provider_reported: float,
    tolerance: float = 0.0,
) -> ReconciliationResult:
    """Reconcile the recorded ledger spend against the provider-reported total for a scope.

    ``delta`` is ``provider_reported - ledger_units``; the reconciliation is ``within_tolerance``
    when its absolute value is within the approved ``tolerance`` (0 => exact match required).
    """
    if tolerance < 0:
        raise AiInvariantViolation("tolerance must be non-negative", code="ai.cost.tolerance")
    delta = round(provider_reported - ledger_units, 12)
    return ReconciliationResult(
        scope=scope,
        scope_id=scope_id,
        ledger_units=ledger_units,
        provider_reported=provider_reported,
        delta=delta,
        within_tolerance=abs(delta) <= tolerance,
    )


__all__ = [
    "BudgetDecision",
    "BudgetLimit",
    "BudgetScope",
    "CostEntry",
    "ReconciliationResult",
    "evaluate_budget",
    "reconcile_cost",
]
