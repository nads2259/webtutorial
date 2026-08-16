"""In-memory reference policy evaluator (deny-by-default).

A minimal, deterministic :class:`PolicyDecisionPort` used to prove the pipeline end to end
(LAW-01) without a real policy engine (that is a separate module, LAW-02). It grants nothing
unless an explicit :class:`~northstar.kernel.policy.ports.PolicyGrant` matches, so the default
answer is always deny with an explainable reason (rule 50, LAW-08).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable

from ..context import RequestContext, ResourceRef
from .ports import (
    PolicyDecision,
    PolicyEffect,
    PolicyGrant,
    PolicyReason,
)

_DENY_CODE = "POLICY_NO_MATCHING_GRANT"
_ALLOW_CODE = "POLICY_GRANT_MATCHED"


def _new_decision_id() -> str:
    return f"pdc_{uuid.uuid4().hex}"


class InMemoryPolicyEvaluator:
    """Deny-by-default evaluator backed by an explicit allowlist of grants.

    Grants are injected at construction (dependency inversion, rule 20). No grant means no
    access; a request is allowed only when at least one grant matches the actor, action and
    resource. Injecting ``id_factory`` keeps ``decision_id`` deterministic under test.
    """

    def __init__(
        self,
        grants: Iterable[PolicyGrant] = (),
        *,
        policy_version: str = "reference-0.1.0",
        id_factory: Callable[[], str] = _new_decision_id,
    ) -> None:
        self._grants: tuple[PolicyGrant, ...] = tuple(grants)
        self._policy_version = policy_version
        self._id_factory = id_factory

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def grant(self, grant: PolicyGrant) -> None:
        """Add a positive authorization rule."""
        self._grants = (*self._grants, grant)

    def decide(
        self,
        context: RequestContext,
        action: str,
        resource: ResourceRef | None,
    ) -> PolicyDecision:
        actor_id = context.actor.id
        matched = any(g.matches(actor_id, action, resource) for g in self._grants)
        if matched:
            return PolicyDecision(
                decision_id=self._id_factory(),
                effect=PolicyEffect.ALLOW,
                action=action,
                reasons=(
                    PolicyReason(
                        code=_ALLOW_CODE,
                        message=f"an explicit grant authorizes '{action}'",
                    ),
                ),
            )
        return PolicyDecision(
            decision_id=self._id_factory(),
            effect=PolicyEffect.DENY,
            action=action,
            reasons=(
                PolicyReason(
                    code=_DENY_CODE,
                    message=(
                        f"no grant authorizes actor '{actor_id}' to perform '{action}'; "
                        "access is denied by default"
                    ),
                ),
            ),
        )
