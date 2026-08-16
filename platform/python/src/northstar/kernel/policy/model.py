"""Value objects for the layered authorization model (docs/07 §5-12, rule 50, LAW-08).

Pure, stdlib-only (LAW-02): the kernel never imports infrastructure. These types describe the
inputs the :class:`~northstar.kernel.policy.layered.LayeredPolicyEvaluator` combines —
RBAC roles, relationship tuples, ABAC resource attributes, entitlement-gated actions — plus the
time-bounded impersonation and break-glass grants (FR-IDN-007/008). Tenant scope is derived from
the authenticated context, never from a request payload (rule 50); an action whose resource scope
is tenant-bound *fails closed* when that scope is missing or ambiguous (FR-POL-003).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# ---------------------------------------------------------------------------
# Stable reason codes (explainable, non-disclosing — FR-POL-002)
# ---------------------------------------------------------------------------

REASON_DENY_DEFAULT = "POLICY_DENY_DEFAULT"
REASON_RBAC_GRANT = "POLICY_RBAC_GRANT"
REASON_RELATIONSHIP_GRANT = "POLICY_RELATIONSHIP_GRANT"
REASON_SIMPLE_GRANT = "POLICY_GRANT_MATCHED"
REASON_ABAC_CONSTRAINT = "POLICY_ABAC_CONSTRAINT"
REASON_ABAC_DENY = "POLICY_ABAC_DENY"
REASON_FAIL_CLOSED_NO_TENANT = "POLICY_FAIL_CLOSED_NO_TENANT_SCOPE"
REASON_FAIL_CLOSED_NO_RESOURCE = "POLICY_FAIL_CLOSED_MISSING_RESOURCE"
REASON_FAIL_CLOSED_AMBIGUOUS = "POLICY_FAIL_CLOSED_AMBIGUOUS_SCOPE"
REASON_CROSS_TENANT = "POLICY_CROSS_TENANT_DENIED"
REASON_IMPERSONATION_GRANT = "POLICY_IMPERSONATION_GRANT"
REASON_IMPERSONATION_NOT_AUTHORIZED = "POLICY_IMPERSONATION_NOT_AUTHORIZED"
REASON_IMPERSONATION_BLOCKED = "POLICY_IMPERSONATION_BLOCKED_ACTION"
REASON_BREAK_GLASS_GRANT = "POLICY_BREAK_GLASS_GRANT"
REASON_ENTITLEMENT_DENIED = "POLICY_ENTITLEMENT_DENIED"

# Generic, client-safe deny message. Detailed causes live in reason *codes* + the audit trail,
# never in text that could disclose rule content or another tenant's data (FR-POL-002).
GENERIC_DENY_MESSAGE = "the requested action is not permitted in this scope"

# ---------------------------------------------------------------------------
# Obligation type identifiers (docs/07 §7)
# ---------------------------------------------------------------------------

OBLIGATION_DUAL_ACTOR_AUDIT = "dual_actor_audit"
OBLIGATION_ENHANCED_EVIDENCE = "record_enhanced_evidence"
OBLIGATION_BREAK_GLASS = "break_glass"
OBLIGATION_REDACT_FIELDS = "redact_fields"
OBLIGATION_RESTRICT_EXPORT = "restrict_export"


class ResourceScope(StrEnum):
    """Where a resource lives, per the ``policy-decision`` ``permission.resource_scope`` enum."""

    GLOBAL = "global"
    PRODUCT = "product"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    OWNED = "owned"
    EXPLICIT = "explicit"


# Scopes that bind a resource to a tenant/owner and therefore MUST fail closed when the tenant or
# resource scope is missing or ambiguous (FR-POL-003).
TENANT_BOUND_SCOPES: frozenset[ResourceScope] = frozenset(
    {
        ResourceScope.ORGANIZATION,
        ResourceScope.WORKSPACE,
        ResourceScope.OWNED,
        ResourceScope.EXPLICIT,
    }
)

# Action categories that remain blocked even for an authorized impersonation session (docs/07 §11):
# financial, credential, secret and private-note actions never run under "act as user".
IMPERSONATION_BLOCKED_PREFIXES: tuple[str, ...] = (
    "commerce.",
    "billing.",
    "payment.",
    "identity.credential.",
    "identity.mfa.",
    "secret.",
    "annotation.private.",
)


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """A tenant/organization scope (``policy-decision`` ``scopeRef``): org/workspace/product."""

    organization_id: str | None = None
    workspace_id: str | None = None
    product_id: str | None = None

    def covers(self, other: ScopeRef) -> bool:
        """True when this (grant) scope authorizes access to ``other`` (a resource's scope).

        A ``None`` component is a wildcard: a role bound at the organization level (workspace
        ``None``) covers any workspace within that organization; a fully-``None`` scope is global.
        """
        if self.organization_id is not None and self.organization_id != other.organization_id:
            return False
        if self.workspace_id is not None and self.workspace_id != other.workspace_id:
            return False
        return self.product_id is None or self.product_id == other.product_id


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    """Declares an action's authorization requirements (registered per module, docs/07 §6).

    ``resource_scope`` decides whether the action is tenant-bound (and thus fails closed without a
    resolvable tenant). ``requires_entitlement`` gates the action on a commercial grant *without*
    the policy engine ever learning a plan/payment name (ARCH-019, FR-POL-005).
    """

    action: str
    resource_scope: ResourceScope
    requires_entitlement: bool = False

    @property
    def is_tenant_bound(self) -> bool:
        return self.resource_scope in TENANT_BOUND_SCOPES


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """A role as a permission *bundle* — never a hardcoded conditional (docs/07 §5)."""

    name: str
    actions: frozenset[str] = field(default_factory=frozenset)

    def grants(self, action: str) -> bool:
        return action in self.actions


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """Binds a role to an actor within a scope (RBAC assignment)."""

    actor_id: str
    role: str
    scope: ScopeRef = field(default_factory=ScopeRef)


@dataclass(frozen=True, slots=True)
class RelationGrant:
    """Maps a relationship (owner/reviewer/…) to the actions it authorizes (docs/07 §5)."""

    relation: str
    actions: frozenset[str] = field(default_factory=frozenset)

    def grants(self, action: str) -> bool:
        return action in self.actions


@dataclass(frozen=True, slots=True)
class RelationshipTuple:
    """An actor's relationship to a specific resource (owner, reviewer, team_member, …)."""

    actor_id: str
    resource_type: str
    resource_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class ResourceAttributes:
    """ABAC attributes of a resource, incl. the tenant scope used for fail-closed checks.

    ``tenant`` is the authoritative scope of the resource (derived server-side); ``owner_id``
    supports ``OWNED`` relationship checks; ``classification`` and ``state`` feed ABAC rules.
    """

    tenant: ScopeRef | None = None
    owner_id: str | None = None
    classification: str = "internal"
    state: str = "active"
    extra: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SeparationOfDutyRule:
    """A set of roles that must not be held together (custom-role SoD validation, docs/07 §5)."""

    name: str
    conflicting_roles: frozenset[str]

    def violated_by(self, roles: frozenset[str]) -> bool:
        return len(self.conflicting_roles & roles) >= 2


@dataclass(frozen=True, slots=True)
class ImpersonationGrant:
    """A time-bounded support-impersonation grant (FR-IDN-007, docs/07 §11).

    An operator impersonates ``effective_subject_id`` within ``scope`` until ``expires_at``, tied
    to a support ``ticket``. Every action under it is dual-actor audited; sensitive categories stay
    blocked (:data:`IMPERSONATION_BLOCKED_PREFIXES`).
    """

    operator_id: str
    effective_subject_id: str
    scope: ScopeRef
    expires_at: datetime
    ticket: str

    def active(self, now: datetime) -> bool:
        return now < self.expires_at

    def matches(self, *, operator_id: str, effective_subject_id: str) -> bool:
        return self.operator_id == operator_id and self.effective_subject_id == effective_subject_id


@dataclass(frozen=True, slots=True)
class BreakGlassGrant:
    """An exceptional, elevated-authority emergency grant (FR-IDN-008, docs/07 §12).

    Requires a second-party ``authorized_by`` (dual authorization), is short-lived (``expires_at``)
    and scoped; it can never disable audit — the bus records it, and it carries enhanced-evidence
    and break-glass obligations.
    """

    operator_id: str
    scope: ScopeRef
    expires_at: datetime
    authorized_by: str

    def active(self, now: datetime) -> bool:
        return now < self.expires_at and bool(self.authorized_by)


def is_impersonation_blocked(action: str) -> bool:
    """True when ``action`` may never run under an impersonation session (docs/07 §11)."""
    return action.startswith(IMPERSONATION_BLOCKED_PREFIXES)
