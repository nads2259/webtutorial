"""Deny-by-default layered authorization evaluator (docs/07 §5, rule 50, LAW-08).

The :class:`LayeredPolicyEvaluator` implements the kernel :class:`PolicyDecisionPort` by combining,
in order and deny-by-default:

1. **Fail-closed tenant/resource scoping** (FR-POL-003) — a tenant-bound action denies immediately
   when the authenticated tenant scope, the target resource, or the resource's own tenant is
   missing or ambiguous, and denies cross-tenant access outright.
2. **Impersonation / break-glass** (FR-IDN-007/008) — time-bounded, dual-actor-audited operator
   grants; sensitive categories stay blocked under impersonation.
3. **RBAC** — role bundles bound to the actor within a covering scope (docs/07 §5).
4. **Relationship / resource authorization** — owner/reviewer/team-member relations.
5. **ABAC** — attribute rules that may add obligations (redaction, export limits) or deny.
6. **Entitlement gating** — asks an :class:`EntitlementCheckPort`; never sees plan/payment names
   (ARCH-019, FR-POL-005).

Actions not registered as governed :class:`ActionDefinition`\\s fall back to the simple explicit
grant model (kept identical to :class:`InMemoryPolicyEvaluator`) so existing capabilities keep
working. Every decision is explainable via stable reason codes and obligations, but deny messages
are generic and never disclose rule content or another tenant's data (FR-POL-002).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ..context import RequestContext, ResourceRef
from .model import (
    GENERIC_DENY_MESSAGE,
    OBLIGATION_BREAK_GLASS,
    OBLIGATION_DUAL_ACTOR_AUDIT,
    OBLIGATION_ENHANCED_EVIDENCE,
    REASON_ABAC_DENY,
    REASON_BREAK_GLASS_GRANT,
    REASON_CROSS_TENANT,
    REASON_DENY_DEFAULT,
    REASON_ENTITLEMENT_DENIED,
    REASON_FAIL_CLOSED_AMBIGUOUS,
    REASON_FAIL_CLOSED_NO_RESOURCE,
    REASON_FAIL_CLOSED_NO_TENANT,
    REASON_IMPERSONATION_BLOCKED,
    REASON_IMPERSONATION_NOT_AUTHORIZED,
    REASON_RBAC_GRANT,
    REASON_RELATIONSHIP_GRANT,
    REASON_SIMPLE_GRANT,
    ActionDefinition,
    BreakGlassGrant,
    ImpersonationGrant,
    RelationGrant,
    ResourceAttributes,
    RoleBinding,
    RoleDefinition,
    is_impersonation_blocked,
)
from .ports import (
    PolicyDecision,
    PolicyEffect,
    PolicyGrant,
    PolicyObligation,
    PolicyReason,
)


@runtime_checkable
class RoleBindingProviderPort(Protocol):
    """Resolves the RBAC role bindings held by an actor."""

    def bindings_for(self, actor_id: str) -> Sequence[RoleBinding]: ...


@runtime_checkable
class RelationshipProviderPort(Protocol):
    """Resolves the relationship names an actor holds toward a specific resource."""

    def relationships_for(self, actor_id: str, resource: ResourceRef) -> Sequence[str]: ...


@runtime_checkable
class ResourceAttributeProviderPort(Protocol):
    """Resolves a resource's authoritative attributes (tenant, owner, classification, state)."""

    def attributes_for(self, resource: ResourceRef) -> ResourceAttributes | None: ...


@runtime_checkable
class EntitlementCheckPort(Protocol):
    """Answers whether an actor holds a commercial entitlement for an action (ARCH-019).

    Deliberately boolean and plan-name-free: the policy engine never learns *which* plan, order or
    payment produced the grant — only whether an active grant exists (FR-POL-005).
    """

    def is_entitled(self, actor_id: str, action: str, resource: ResourceRef | None) -> bool: ...


@dataclass(frozen=True, slots=True)
class AbacVerdict:
    """The outcome of an ABAC rule: an optional deny plus obligations to attach on allow."""

    deny: bool = False
    obligations: tuple[PolicyObligation, ...] = ()


AbacRule = Callable[[RequestContext, ActionDefinition, ResourceAttributes], AbacVerdict]


class InMemoryRoleDirectory:
    """In-memory RBAC binding provider (dependency-injected; no infrastructure)."""

    def __init__(self, bindings: Iterable[RoleBinding] = ()) -> None:
        self._bindings: list[RoleBinding] = list(bindings)

    def bind(self, binding: RoleBinding) -> None:
        self._bindings.append(binding)

    def bindings_for(self, actor_id: str) -> Sequence[RoleBinding]:
        return [b for b in self._bindings if b.actor_id == actor_id]


class InMemoryRelationshipGraph:
    """In-memory relationship provider keyed by ``(actor, resource_type, resource_id)``."""

    def __init__(self, tuples: Iterable[tuple[str, str, str, str]] = ()) -> None:
        # (actor_id, resource_type, resource_id) -> {relations}
        self._by_key: dict[tuple[str, str, str], set[str]] = {}
        for actor_id, rtype, rid, relation in tuples:
            self.add(actor_id, rtype, rid, relation)

    def add(self, actor_id: str, resource_type: str, resource_id: str, relation: str) -> None:
        self._by_key.setdefault((actor_id, resource_type, resource_id), set()).add(relation)

    def relationships_for(self, actor_id: str, resource: ResourceRef) -> Sequence[str]:
        return sorted(self._by_key.get((actor_id, resource.type, resource.id), set()))


class InMemoryResourceAttributes:
    """In-memory resource-attribute provider keyed by ``(resource_type, resource_id)``."""

    def __init__(
        self, attributes: Mapping[tuple[str, str], ResourceAttributes] | None = None
    ) -> None:
        self._by_key: dict[tuple[str, str], ResourceAttributes] = dict(attributes or {})

    def put(self, resource: ResourceRef, attributes: ResourceAttributes) -> None:
        self._by_key[(resource.type, resource.id)] = attributes

    def attributes_for(self, resource: ResourceRef) -> ResourceAttributes | None:
        return self._by_key.get((resource.type, resource.id))


def _new_decision_id() -> str:
    return f"pdc_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LayeredPolicyEvaluator:
    """Deny-by-default authorization engine layering RBAC + relationship + ABAC (docs/07 §5)."""

    def __init__(
        self,
        *,
        action_definitions: Iterable[ActionDefinition] = (),
        role_definitions: Iterable[RoleDefinition] = (),
        relation_grants: Iterable[RelationGrant] = (),
        abac_rules: Iterable[AbacRule] = (),
        simple_grants: Iterable[PolicyGrant] = (),
        roles: RoleBindingProviderPort | None = None,
        relationships: RelationshipProviderPort | None = None,
        resources: ResourceAttributeProviderPort | None = None,
        entitlements: EntitlementCheckPort | None = None,
        impersonation_grants: Iterable[ImpersonationGrant] = (),
        break_glass_grants: Iterable[BreakGlassGrant] = (),
        policy_version: str = "layered-1.0.0",
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_decision_id,
    ) -> None:
        self._actions: dict[str, ActionDefinition] = {a.action: a for a in action_definitions}
        self._roles_def: dict[str, RoleDefinition] = {r.name: r for r in role_definitions}
        self._relation_grants: dict[str, RelationGrant] = {g.relation: g for g in relation_grants}
        self._abac_rules: tuple[AbacRule, ...] = tuple(abac_rules)
        self._simple_grants: tuple[PolicyGrant, ...] = tuple(simple_grants)
        self._roles = roles or InMemoryRoleDirectory()
        self._relationships = relationships or InMemoryRelationshipGraph()
        self._resources = resources or InMemoryResourceAttributes()
        self._entitlements = entitlements
        self._impersonation = tuple(impersonation_grants)
        self._break_glass = tuple(break_glass_grants)
        self._policy_version = policy_version
        self._clock = clock
        self._id_factory = id_factory

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def grant(self, grant: PolicyGrant) -> None:
        """Add a simple explicit grant (backward-compatible with the reference evaluator)."""
        self._simple_grants = (*self._simple_grants, grant)

    # -- decision -----------------------------------------------------------------

    def decide(
        self,
        context: RequestContext,
        action: str,
        resource: ResourceRef | None,
    ) -> PolicyDecision:
        definition = self._actions.get(action)
        if definition is None:
            return self._decide_simple(context, action, resource)
        return self._decide_governed(context, definition, resource)

    # -- unregistered actions: explicit-grant allowlist (deny-by-default) ----------

    def _decide_simple(
        self, context: RequestContext, action: str, resource: ResourceRef | None
    ) -> PolicyDecision:
        actor_id = context.actor.id
        if any(g.matches(actor_id, action, resource) for g in self._simple_grants):
            return self._allow(
                action, REASON_SIMPLE_GRANT, "an explicit grant authorizes the action"
            )
        return self._deny(action, REASON_DENY_DEFAULT)

    # -- governed actions: fail-closed + layered evaluation ------------------------

    def _decide_governed(
        self,
        context: RequestContext,
        definition: ActionDefinition,
        resource: ResourceRef | None,
    ) -> PolicyDecision:
        action = definition.action
        attributes: ResourceAttributes | None = None

        if definition.is_tenant_bound:
            fail_closed = self._fail_closed(context, definition, resource)
            if fail_closed is not None:
                return fail_closed
            # resource is guaranteed non-None past the fail-closed gate
            if resource is not None:
                attributes = self._resources.attributes_for(resource)

        now = self._clock()
        actor = context.actor

        # Break-glass: emergency, elevated-authority operator override (still scoped + audited).
        if actor.type == "operator" and actor.delegated_by is None:
            bg = self._active_break_glass(actor.id, attributes, now)
            if bg is not None:
                return self._allow(
                    action,
                    REASON_BREAK_GLASS_GRANT,
                    "an authorized break-glass session permits the action",
                    obligations=(
                        PolicyObligation(type=OBLIGATION_BREAK_GLASS),
                        PolicyObligation(type=OBLIGATION_ENHANCED_EVIDENCE),
                    ),
                )

        # Impersonation: an operator acting *as* a user (actor.delegated_by set).
        impersonation_obligations: tuple[PolicyObligation, ...] = ()
        if actor.delegated_by is not None:
            grant = self._active_impersonation(
                operator_id=actor.delegated_by,
                effective_subject_id=actor.id,
                attributes=attributes,
                now=now,
            )
            if grant is None:
                return self._deny(action, REASON_IMPERSONATION_NOT_AUTHORIZED)
            if is_impersonation_blocked(action):
                return self._deny(action, REASON_IMPERSONATION_BLOCKED)
            impersonation_obligations = (PolicyObligation(type=OBLIGATION_DUAL_ACTOR_AUDIT),)

        # RBAC + relationship (deny-by-default: at least one must grant).
        rbac = self._rbac_grants(actor.id, action, attributes)
        relationship = self._relationship_grants(actor.id, action, resource, attributes)
        if not rbac and not relationship:
            return self._deny(action, REASON_DENY_DEFAULT)

        # Entitlement gate (never learns plan/payment names — ARCH-019).
        if (
            definition.requires_entitlement
            and self._entitlements is not None
            and not self._entitlements.is_entitled(actor.id, action, resource)
        ):
            return self._deny(action, REASON_ENTITLEMENT_DENIED)

        # ABAC: may deny outright or attach obligations.
        obligations: list[PolicyObligation] = list(impersonation_obligations)
        if attributes is not None:
            for rule in self._abac_rules:
                verdict = rule(context, definition, attributes)
                if verdict.deny:
                    return self._deny(action, REASON_ABAC_DENY)
                obligations.extend(verdict.obligations)

        reason_code = REASON_RBAC_GRANT if rbac else REASON_RELATIONSHIP_GRANT
        return self._allow(
            action,
            reason_code,
            "an authorized role or relationship permits the action",
            obligations=tuple(obligations),
        )

    # -- fail-closed scoping (FR-POL-003) ------------------------------------------

    def _fail_closed(
        self,
        context: RequestContext,
        definition: ActionDefinition,
        resource: ResourceRef | None,
    ) -> PolicyDecision | None:
        if not context.tenant_scope:
            return self._deny(definition.action, REASON_FAIL_CLOSED_NO_TENANT)
        if resource is None:
            return self._deny(definition.action, REASON_FAIL_CLOSED_NO_RESOURCE)
        attributes = self._resources.attributes_for(resource)
        if (
            attributes is None
            or attributes.tenant is None
            or attributes.tenant.organization_id is None
        ):
            return self._deny(definition.action, REASON_FAIL_CLOSED_AMBIGUOUS)
        if attributes.tenant.organization_id != context.tenant_scope:
            return self._deny(definition.action, REASON_CROSS_TENANT)
        return None

    # -- layers --------------------------------------------------------------------

    def _rbac_grants(
        self, actor_id: str, action: str, attributes: ResourceAttributes | None
    ) -> bool:
        resource_scope = attributes.tenant if attributes else None
        for binding in self._roles.bindings_for(actor_id):
            role = self._roles_def.get(binding.role)
            if role is None or not role.grants(action):
                continue
            if resource_scope is None or binding.scope.covers(resource_scope):
                return True
        return False

    def _relationship_grants(
        self,
        actor_id: str,
        action: str,
        resource: ResourceRef | None,
        attributes: ResourceAttributes | None,
    ) -> bool:
        # Ownership: the resource owner implicitly holds the "owner" relation.
        if attributes is not None and attributes.owner_id == actor_id:
            owner_grant = self._relation_grants.get("owner")
            if owner_grant is not None and owner_grant.grants(action):
                return True
        if resource is None:
            return False
        for relation in self._relationships.relationships_for(actor_id, resource):
            grant = self._relation_grants.get(relation)
            if grant is not None and grant.grants(action):
                return True
        return False

    def _active_impersonation(
        self,
        *,
        operator_id: str,
        effective_subject_id: str,
        attributes: ResourceAttributes | None,
        now: datetime,
    ) -> ImpersonationGrant | None:
        target = attributes.tenant if attributes else None
        for grant in self._impersonation:
            if not grant.matches(
                operator_id=operator_id, effective_subject_id=effective_subject_id
            ):
                continue
            if not grant.active(now):
                continue
            if target is not None and not grant.scope.covers(target):
                continue
            return grant
        return None

    def _active_break_glass(
        self, operator_id: str, attributes: ResourceAttributes | None, now: datetime
    ) -> BreakGlassGrant | None:
        target = attributes.tenant if attributes else None
        for grant in self._break_glass:
            if grant.operator_id != operator_id or not grant.active(now):
                continue
            if target is not None and not grant.scope.covers(target):
                continue
            return grant
        return None

    # -- decision builders ---------------------------------------------------------

    def _allow(
        self,
        action: str,
        reason_code: str,
        message: str,
        *,
        obligations: tuple[PolicyObligation, ...] = (),
    ) -> PolicyDecision:
        return PolicyDecision(
            decision_id=self._id_factory(),
            effect=PolicyEffect.ALLOW,
            action=action,
            reasons=(PolicyReason(code=reason_code, message=message),),
            obligations=obligations,
        )

    def _deny(self, action: str, reason_code: str) -> PolicyDecision:
        # The message is deliberately generic (FR-POL-002): the stable *code* carries the machine
        # meaning for internal audit; the text never leaks rule content or another tenant's data.
        return PolicyDecision(
            decision_id=self._id_factory(),
            effect=PolicyEffect.DENY,
            action=action,
            reasons=(PolicyReason(code=reason_code, message=GENERIC_DENY_MESSAGE),),
        )


def restricted_classification_rule(
    _context: RequestContext, _definition: ActionDefinition, attributes: ResourceAttributes
) -> AbacVerdict:
    """Reference ABAC rule: redact ``restricted`` resources (docs/07 §7)."""
    if attributes.classification == "restricted":
        return AbacVerdict(obligations=(PolicyObligation(type="redact_fields"),))
    return AbacVerdict()
