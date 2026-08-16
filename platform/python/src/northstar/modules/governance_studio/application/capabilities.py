"""Governance Studio capabilities: composition + audit exploration (LAW-04, ARCH-005/022).

Two authoritative read capabilities, both routed through the kernel query bus:

* ``studio.compose`` projects the registered contributions into a **role/scope-filtered**
  :class:`NavigationModel` (FR-CMS-002). Projection asks the kernel policy engine for a decision on
  every workbench's ``required_permissions``; a surface the actor may not use is simply omitted.
  Omission is a usability affordance, not the authorization boundary — invoking the omitted action
  still fails closed at the capability layer.
* ``studio.audit.explore`` reads recorded audit evidence and correlates it by request/correlation id
  within the caller's tenant scope (FR-CMS-006), disclosing nothing from other tenants.

The Studio writes no domain tables: these handlers only consult the injected policy port, the
contribution registry and the audit reader port.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from northstar.kernel.audit.ports import AuditRecord
from northstar.kernel.context import RequestContext
from northstar.kernel.policy.ports import PolicyDecisionPort

from ..domain.model import STUDIO_API_VERSION, DangerLevel, NavigationModel, StudioContribution
from .ports import AuditReaderPort, SurfaceResourceResolver
from .registry import ContributionRegistry

CAP_VERSION = "1.0.0"

CAP_COMPOSE_STUDIO = "studio.compose"
CAP_EXPLORE_AUDIT = "studio.audit.explore"


# ---------------------------------------------------------------------------
# Compose (studio.compose) — role/scope-projected navigation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComposeStudioQuery:
    """Parameters for composing the Studio for the authenticated actor (context-derived)."""


@dataclass(frozen=True, slots=True)
class WorkbenchView:
    id: str
    route: str
    component: str
    required_permissions: tuple[str, ...]
    danger_level: str


@dataclass(frozen=True, slots=True)
class NavNodeView:
    id: str
    label_key: str
    workbench_id: str
    order: int
    icon: str | None


@dataclass(frozen=True, slots=True)
class ComposedStudioResult:
    """The projected, versioned navigation returned to the shell."""

    studio_api: str
    navigation_version: str
    navigation_revision: str
    nodes: tuple[NavNodeView, ...]
    workbenches: tuple[WorkbenchView, ...]


class SurfaceProjection:
    """Projects contributions into a navigation model filtered by the policy engine (FR-CMS-002).

    A workbench is included only when the actor is authorized for **every** one of its
    ``required_permissions`` (deny-by-default). Navigation nodes are included only when their target
    workbench survived projection, so the shell never renders a link the actor cannot follow.
    """

    def __init__(
        self, *, policy: PolicyDecisionPort, resource_resolver: SurfaceResourceResolver
    ) -> None:
        self._policy = policy
        self._resolve = resource_resolver

    def project(
        self, contributions: Sequence[StudioContribution], context: RequestContext
    ) -> NavigationModel:
        allowed_workbenches = []
        allowed_ids: set[str] = set()
        for contribution in contributions:
            for workbench in contribution.workbenches:
                if self._actor_may_use(workbench.required_permissions, context):
                    allowed_workbenches.append(workbench)
                    allowed_ids.add(workbench.id)

        nodes = [
            node
            for contribution in contributions
            for node in contribution.navigation
            if node.workbench_id in allowed_ids
        ]
        nodes.sort(key=lambda n: (n.order, n.id))
        allowed_workbenches.sort(key=lambda w: w.id)
        return NavigationModel(
            version=STUDIO_API_VERSION,
            nodes=tuple(nodes),
            workbenches=tuple(allowed_workbenches),
        )

    def _actor_may_use(self, permissions: Sequence[str], context: RequestContext) -> bool:
        for action in permissions:
            resource = self._resolve(context, action)
            decision = self._policy.decide(context, action, resource)
            if not decision.allowed:
                return False
        return True


class ComposeStudio:
    """``studio.compose`` (query) — return the projected navigation for the authenticated actor."""

    def __init__(self, *, registry: ContributionRegistry, projection: SurfaceProjection) -> None:
        self._registry = registry
        self._projection = projection

    def handle(self, request: object) -> ComposedStudioResult:
        context = _context(request)
        model = self._projection.project(self._registry.contributions(), context)
        return ComposedStudioResult(
            studio_api=model.version,
            navigation_version=model.version,
            navigation_revision=model.revision,
            nodes=tuple(
                NavNodeView(
                    id=n.id,
                    label_key=n.label_key,
                    workbench_id=n.workbench_id,
                    order=n.order,
                    icon=n.icon,
                )
                for n in model.nodes
            ),
            workbenches=tuple(
                WorkbenchView(
                    id=w.id,
                    route=w.route,
                    component=w.component,
                    required_permissions=w.required_permissions,
                    danger_level=(
                        w.danger_level.value
                        if isinstance(w.danger_level, DangerLevel)
                        else str(w.danger_level)
                    ),
                )
                for w in model.workbenches
            ),
        )


# ---------------------------------------------------------------------------
# Audit exploration (studio.audit.explore) — correlation across requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExploreAuditQuery:
    """Explore audit evidence, optionally narrowed to one ``correlation_id`` (FR-CMS-006)."""

    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEntryView:
    evidence_id: str
    event_type: str
    occurred_at: str
    actor_type: str
    actor_id: str
    action: str
    outcome: str
    correlation_id: str
    resource_type: str | None
    resource_id: str | None
    decision_ref: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditExplorationResult:
    """Correlated audit entries scoped to the caller's tenant (least-disclosure, FR-CMS-006)."""

    tenant_scope: str | None
    correlation_id: str | None
    entries: tuple[AuditEntryView, ...] = field(default_factory=tuple)


class ExploreAudit:
    """``studio.audit.explore`` (query) — correlate recorded evidence within the caller's tenant."""

    def __init__(self, *, reader: AuditReaderPort) -> None:
        self._reader = reader

    def handle(self, request: object) -> AuditExplorationResult:
        context = _context(request)
        params = getattr(request, "parameters", None)
        correlation_id = getattr(params, "correlation_id", None)
        tenant = context.tenant_scope

        entries = tuple(
            _to_view(record)
            for record in self._reader.records()
            if _visible_to_tenant(record, tenant)
            and (correlation_id is None or record.correlation_id == correlation_id)
        )
        return AuditExplorationResult(
            tenant_scope=tenant, correlation_id=correlation_id, entries=entries
        )


def _visible_to_tenant(record: AuditRecord, tenant: str | None) -> bool:
    """Only disclose evidence whose resource is scoped to the caller's tenant (no cross-tenant)."""
    if tenant is None or record.resource is None:
        return False
    return record.resource.id == tenant


def _to_view(record: AuditRecord) -> AuditEntryView:
    return AuditEntryView(
        evidence_id=record.evidence_id,
        event_type=record.event_type,
        occurred_at=record.occurred_at,
        actor_type=record.actor.type.value,
        actor_id=record.actor.id,
        action=record.action,
        outcome=record.outcome.value,
        correlation_id=record.correlation_id,
        resource_type=None if record.resource is None else record.resource.type,
        resource_id=None if record.resource is None else record.resource.id,
        decision_ref=record.decision_ref,
        reason_codes=tuple(record.reason_codes),
    )


def _context(request: object) -> RequestContext:
    context = getattr(request, "context", None)
    if not isinstance(context, RequestContext):
        raise TypeError("a RequestContext is required to compose the Studio")
    return context
