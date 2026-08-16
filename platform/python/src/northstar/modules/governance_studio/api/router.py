"""``/studio`` FastAPI router — the Governance Studio control-plane edge (LAW-05, docs/13 §2).

Every endpoint is a thin inbound adapter over the kernel command/query buses (LAW-04). The Studio is
an authorized *client*, never a database backdoor: it composes surfaces and proxies actions to
**registered capabilities**, writing no domain tables. The authenticated :class:`RequestContext`
(including tenant scope) comes from an injected authenticator (server-side session), never from the
request body or a client header (rule 50). Policy denials surface as
``403 application/problem+json`` — an action hidden from the composed navigation still fails closed
when invoked directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus
from northstar.modules.organization.application import capabilities as org

from ..application.capabilities import (
    CAP_COMPOSE_STUDIO,
    CAP_EXPLORE_AUDIT,
    CAP_VERSION,
    AuditExplorationResult,
    ComposedStudioResult,
    ComposeStudioQuery,
    ExploreAuditQuery,
)

_STATE_KEY = "northstar_governance_studio_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class GovernanceStudioApiDependencies:
    """Collaborators the ``/studio`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_governance_studio_dependencies(
    app_state: object, deps: GovernanceStudioApiDependencies
) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> GovernanceStudioApiDependencies:
    return getattr(request.app.state, _STATE_KEY)


def _problem(status: int, code: str, detail: str, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type=_PROBLEM_CONTENT_TYPE,
        content={
            "type": f"https://errors.northstar.example/{code.replace('.', '/')}",
            "title": detail,
            "status": status,
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id,
            "retryable": False,
        },
    )


def _org_resource(context: RequestContext) -> ResourceRef | None:
    if not context.tenant_scope:
        return None
    return ResourceRef(type=org.RES_ORGANIZATION, id=context.tenant_scope)


def create_governance_studio_router() -> APIRouter:
    """Build the ``/studio`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/studio", tags=["governance-studio"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    @router.get("/surfaces")
    def surfaces(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_COMPOSE_STUDIO,
            version=CAP_VERSION,
            parameters=ComposeStudioQuery(),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        composed: ComposedStudioResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "studio_api": composed.studio_api,
                "navigation": {
                    "version": composed.navigation_version,
                    "revision": composed.navigation_revision,
                    "nodes": [
                        {
                            "id": n.id,
                            "label_key": n.label_key,
                            "workbench_id": n.workbench_id,
                            "order": n.order,
                            "icon": n.icon,
                        }
                        for n in composed.nodes
                    ],
                },
                "workbenches": [
                    {
                        "id": w.id,
                        "route": w.route,
                        "component": w.component,
                        "required_permissions": list(w.required_permissions),
                        "danger_level": w.danger_level,
                    }
                    for w in composed.workbenches
                ],
            },
        )

    @router.get("/audit")
    def audit(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        correlation_id = request.query_params.get("correlation_id")
        query = Query(
            capability=CAP_EXPLORE_AUDIT,
            version=CAP_VERSION,
            parameters=ExploreAuditQuery(correlation_id=correlation_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        explored: AuditExplorationResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "tenant_scope": explored.tenant_scope,
                "correlation_id": explored.correlation_id,
                "entries": [
                    {
                        "evidence_id": e.evidence_id,
                        "action": e.action,
                        "outcome": e.outcome,
                        "correlation_id": e.correlation_id,
                        "actor": {"type": e.actor_type, "id": e.actor_id},
                        "resource": (
                            None
                            if e.resource_id is None
                            else {"type": e.resource_type, "id": e.resource_id}
                        ),
                        "decision_ref": e.decision_ref,
                        "reason_codes": list(e.reason_codes),
                        "occurred_at": e.occurred_at,
                    }
                    for e in explored.entries
                ],
            },
        )

    @router.get("/organizations/memberships")
    def list_memberships(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=org.CAP_LIST_MEMBERSHIPS,
            version=org.CAP_VERSION,
            parameters=org.ListMembershipsQuery(),
            resource=_org_resource(context),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        listed: org.ListMembershipsResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "organization_id": listed.organization_id,
                "memberships": [
                    {
                        "membership_id": m.membership_id,
                        "subject_id": m.subject_id,
                        "roles": list(m.roles),
                    }
                    for m in listed.memberships
                ],
            },
        )

    @router.post("/organizations/memberships")
    async def add_membership(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        roles = body.get("roles") or []
        command = Command(
            capability=org.CAP_ADD_MEMBERSHIP,
            version=org.CAP_VERSION,
            payload=org.AddMembershipCommand(
                subject_id=str(body.get("subject_id", "")),
                roles=frozenset(str(r) for r in roles),
                workspace_id=body.get("workspace_id"),
                team_id=body.get("team_id"),
            ),
            resource=_org_resource(context),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "organization.invalid", str(exc), context.correlation_id)
        added: org.AddMembershipResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=201,
            content={
                "membership_id": added.membership_id,
                "organization_id": added.organization_id,
                "subject_id": added.subject_id,
                "roles": list(added.roles),
            },
        )

    return router
