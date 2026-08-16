"""Query bus: the single authoritative read path (LAW-04, ARCH-004, FR-KRN-002).

A query is authorized deny-by-default (rule 50) and then routed to its registered capability
through the :class:`CapabilityDispatcher`. Queries do not mutate state, so there is no audit
write or idempotency requirement — but the authorization boundary is identical to the command
bus so there is no unauthorized bypass route.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..capabilities.registry import CapabilityDispatcher
from ..context import RequestContext, ResourceRef
from ..errors import PolicyDenied
from ..policy.ports import PolicyDecision, PolicyDecisionPort


@dataclass(frozen=True, slots=True)
class Query:
    """A read request routed to one registered capability (name + version)."""

    capability: str
    version: str
    parameters: object = None
    resource: ResourceRef | None = None
    action: str | None = None

    @property
    def policy_action(self) -> str:
        return self.action or self.capability


@dataclass(frozen=True, slots=True)
class QueryInvocation:
    """The typed request object a query handler receives (context + parameters)."""

    context: RequestContext
    query: Query

    @property
    def parameters(self) -> object:
        return self.query.parameters


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The outcome of an authorized query: the handler ``value`` plus its ``decision``."""

    value: object
    decision: PolicyDecision


class QueryBus:
    """Authorizes then dispatches queries via the one authoritative path (LAW-04)."""

    def __init__(
        self,
        dispatcher: CapabilityDispatcher,
        policy: PolicyDecisionPort,
    ) -> None:
        self._dispatcher = dispatcher
        self._policy = policy

    def dispatch(self, query: Query, context: RequestContext) -> QueryResult:
        """Authorize then execute the query. Raises :class:`PolicyDenied` on a deny."""
        decision = self._policy.decide(context, query.policy_action, query.resource)
        if not decision.allowed:
            raise PolicyDenied(
                action=query.policy_action,
                decision_id=decision.decision_id,
                reasons=tuple((r.code, r.message) for r in decision.reasons),
            )
        value = self._dispatcher.dispatch(
            query.capability,
            query.version,
            QueryInvocation(context=context, query=query),
        )
        return QueryResult(value=value, decision=decision)
