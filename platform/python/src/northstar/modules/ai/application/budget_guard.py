"""Multi-scope AI cost/budget guard — the authoritative cost-enforcement seam (FR-AI-008).

The guard is the ONE place a governed AI request is checked against its applicable cost budgets
across MULTIPLE scopes (per-actor, per-tenant, per-workflow/campaign — the per-run tool-call
budget stays in the Tool Broker). It:

* ``authorize`` sums the already-recorded spend per applicable scope, projects the request's cost
  and DENIES with a typed :class:`BudgetScopeExceeded` (+ an audited denial) if ANY scope would be
  breached; it FAILS SAFE — a ledger/limiter error denies the cost-sensitive request rather than
  fail-open (:class:`BudgetLimiterUnavailable`), never silently allowing unbounded consumption;
* ``record`` appends the provider cost per interaction to the ledger (provenance, FR-AI-009);
* ``reconcile`` compares the recorded ledger total against a provider-reported total (LLM10).

It depends only on the pure :mod:`..domain.budgets`, the :class:`BudgetLedgerPort` and the kernel
audit recorder; it reaches no infrastructure directly (rule 10, LAW-09).
"""

from __future__ import annotations

from collections.abc import Callable

from northstar.kernel.audit.ports import AuditOutcome, AuditRecorderPort
from northstar.kernel.context import Actor, ResourceRef

from ..domain.budgets import (
    BudgetScope,
    CostEntry,
    ReconciliationResult,
    evaluate_budget,
    reconcile_cost,
)
from ..domain.errors import BudgetLimiterUnavailable, BudgetScopeExceeded
from .ports import BudgetLedgerPort

IdFactory = Callable[[], str]

_RES_AI_BUDGET = "ai.budget"
_EVENT_BUDGET = "northstar.ai.budget.decision"
_ACTION_BUDGET = "ai.budget.enforce"


class BudgetGuard:
    """Authoritative multi-scope AI cost/budget enforcement (deny-by-default, fail-safe)."""

    def __init__(
        self,
        *,
        ledger: BudgetLedgerPort,
        audit: AuditRecorderPort,
        id_factory: IdFactory,
    ) -> None:
        self._ledger = ledger
        self._audit = audit
        self._id = id_factory

    def authorize(
        self,
        *,
        organization_id: str,
        actor: Actor,
        actor_id: str,
        workflow_id: str | None,
        requested_cost: float,
        correlation_id: str,
    ) -> None:
        """Deny (typed + audited) if the request's projected spend breaches any applicable budget.

        ``actor_id`` is the AI actor profile id (the per-actor budget subject); ``actor`` is the
        authenticated caller recorded in the audit trail. Any ledger/limiter failure denies the
        cost-sensitive path (fail-safe).
        """
        try:
            limits = self._ledger.limits_for(
                organization_id=organization_id, actor_id=actor_id, workflow_id=workflow_id
            )
            spent: dict[tuple[str, str], float] = {}
            for limit in limits:
                spent[limit.key] = self._ledger.spent(
                    organization_id=organization_id,
                    scope=limit.scope.value,
                    scope_id=limit.scope_id,
                )
        except BudgetScopeExceeded:
            raise
        except Exception as exc:  # fail-safe: a limiter outage denies, never fail-open
            self._audit_decision(
                actor=actor,
                outcome=AuditOutcome.FAILED,
                correlation_id=correlation_id,
                resource_id=organization_id,
                reason_codes=("ai.budget.limiter_unavailable",),
            )
            raise BudgetLimiterUnavailable(str(exc)) from exc

        decision = evaluate_budget(limits=limits, spent=spent, requested_cost=requested_cost)
        if not decision.allowed:
            assert decision.exceeded_scope is not None  # noqa: S101 - narrow for typing
            assert decision.exceeded_scope_id is not None  # noqa: S101
            assert decision.exceeded_limit is not None  # noqa: S101
            assert decision.projected_units is not None  # noqa: S101
            self._audit_decision(
                actor=actor,
                outcome=AuditOutcome.DENIED,
                correlation_id=correlation_id,
                resource_id=decision.exceeded_scope_id,
                reason_codes=(
                    "ai.budget.exceeded",
                    f"scope:{decision.exceeded_scope.value}",
                ),
            )
            raise BudgetScopeExceeded(
                decision.exceeded_scope.value,
                decision.exceeded_scope_id,
                decision.exceeded_limit,
                decision.projected_units,
            )
        self._audit_decision(
            actor=actor,
            outcome=AuditOutcome.SUCCESS,
            correlation_id=correlation_id,
            resource_id=organization_id,
            reason_codes=("ai.budget.allowed",),
        )

    def record(
        self,
        *,
        organization_id: str,
        actor_id: str,
        workflow_id: str | None,
        cost_units: float,
        provider_cost: float,
        provider: str,
        correlation_id: str,
    ) -> CostEntry:
        """Append the provider cost for one interaction to the budget ledger (provenance)."""
        entry = CostEntry(
            entry_id=self._id(),
            organization_id=organization_id,
            actor_id=actor_id,
            workflow_id=workflow_id,
            cost_units=cost_units,
            provider_cost=provider_cost,
            provider=provider,
            correlation_id=correlation_id,
        )
        self._ledger.record(entry)
        return entry

    def reconcile(
        self,
        *,
        organization_id: str,
        scope: BudgetScope,
        scope_id: str,
        provider_reported: float,
        tolerance: float = 0.0,
    ) -> ReconciliationResult:
        """Reconcile the recorded ledger spend for a scope against the provider-reported total."""
        ledger_units = self._ledger.total_recorded(
            organization_id=organization_id, scope=scope.value, scope_id=scope_id
        )
        return reconcile_cost(
            scope=scope,
            scope_id=scope_id,
            ledger_units=ledger_units,
            provider_reported=provider_reported,
            tolerance=tolerance,
        )

    def _audit_decision(
        self,
        *,
        actor: Actor,
        outcome: AuditOutcome,
        correlation_id: str,
        resource_id: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        self._audit.record(
            event_type=_EVENT_BUDGET,
            actor=actor,
            action=_ACTION_BUDGET,
            outcome=outcome,
            correlation_id=correlation_id,
            resource=ResourceRef(type=_RES_AI_BUDGET, id=resource_id),
            reason_codes=reason_codes,
        )


__all__ = ["BudgetGuard"]
