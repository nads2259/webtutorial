"""Policy decision port and typed decision value objects (ARCH-004, FR-KRN-002).

Deny-by-default authorization at the capability layer (rule 50, LAW-08). The kernel consults
a :class:`PolicyDecisionPort` *before* any command executes; the returned
:class:`PolicyDecision` matches the ``policy-decision`` contract shape (``effect``,
``decision_id``, ``reasons``, ``obligations``) so a deny is always explainable. Concrete
policy engines are adapters behind this port (LAW-12); the kernel ships only a reference
in-memory evaluator (see :mod:`northstar.kernel.policy.reference`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..context import RequestContext, ResourceRef


class PolicyEffect(StrEnum):
    """The binary effect of an authorization decision (``policy-decision`` contract)."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyReason:
    """An explainable reason for a decision: a stable ``code`` plus a human ``message``."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PolicyObligation:
    """A condition the caller must honour when acting on an allow (e.g. redaction).

    ``type`` is a stable obligation identifier (``policy-decision`` contract). ``parameters``
    carries obligation-specific, non-sensitive detail (e.g. the fields to redact); it never
    contains rule internals or other tenants' data (FR-POL-002).
    """

    type: str
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """A typed, explainable authorization decision (``policy-decision`` contract shape).

    ``effect`` is allow/deny; ``reasons`` and ``obligations`` make both outcomes auditable;
    ``decision_id`` is the stable identifier an audit record references.
    """

    decision_id: str
    effect: PolicyEffect
    action: str
    reasons: tuple[PolicyReason, ...] = ()
    obligations: tuple[PolicyObligation, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.effect is PolicyEffect.ALLOW

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """The reason codes only — convenient for audit ``reason_codes`` arrays."""
        return tuple(reason.code for reason in self.reasons)


@runtime_checkable
class PolicyDecisionPort(Protocol):
    """Deny-by-default authorization boundary.

    Implementations MUST return an explicit :class:`PolicyDecision`; the pipeline treats the
    absence of an allow as a deny. ``resource`` is ``None`` for actions with no specific target.
    """

    def decide(
        self,
        context: RequestContext,
        action: str,
        resource: ResourceRef | None,
    ) -> PolicyDecision: ...


@dataclass(frozen=True, slots=True)
class PolicyGrant:
    """A single positive authorization rule used by the reference evaluator.

    An empty ``actor_ids`` matches any actor; an empty ``resource_ids`` matches any resource
    of ``resource_type`` (or any resource when ``resource_type`` is ``None``).
    """

    action: str
    actor_ids: frozenset[str] = field(default_factory=frozenset)
    resource_type: str | None = None
    resource_ids: frozenset[str] = field(default_factory=frozenset)

    def matches(self, actor_id: str, action: str, resource: ResourceRef | None) -> bool:
        if action != self.action:
            return False
        if self.actor_ids and actor_id not in self.actor_ids:
            return False
        type_ok = self.resource_type is None or (
            resource is not None and resource.type == self.resource_type
        )
        id_ok = not self.resource_ids or (resource is not None and resource.id in self.resource_ids)
        return type_ok and id_ok
