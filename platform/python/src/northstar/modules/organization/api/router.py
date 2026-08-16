"""``/organizations`` FastAPI router (docs/07 §5, §9).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). Tenant-bound requests pass the organization resource so the layered policy engine
resolves and checks the tenant, failing closed on any mismatch. Policy denials surface as
``403 application/problem+json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus

from ..application.capabilities import (
    CAP_ADD_MEMBERSHIP,
    CAP_CREATE_ORGANIZATION,
    CAP_CREATE_WORKSPACE,
    CAP_LIST_MEMBERSHIPS,
    CAP_VERSION,
    AddMembershipCommand,
    AddMembershipResult,
    CreateOrganizationCommand,
    CreateOrganizationResult,
    CreateWorkspaceCommand,
    CreateWorkspaceResult,
    ListMembershipsQuery,
    ListMembershipsResult,
)
from ..domain.model import RES_ORGANIZATION

_STATE_KEY = "northstar_organization_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

# Maps the inbound request to an authenticated context (or None if unauthenticated).
Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class OrganizationApiDependencies:
    """Collaborators the ``/organizations`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_organization_dependencies(app_state: object, deps: OrganizationApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> OrganizationApiDependencies:
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
    return ResourceRef(type=RES_ORGANIZATION, id=context.tenant_scope)


def create_organization_router() -> APIRouter:
    """Build the ``/organizations`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/organizations", tags=["organization"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    @router.post("")
    async def create_organization(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_CREATE_ORGANIZATION,
            version=CAP_VERSION,
            payload=CreateOrganizationCommand(name=str(body.get("name", ""))),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "organization.invalid", str(exc), context.correlation_id)
        created: CreateOrganizationResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=201,
            content={"organization_id": created.organization_id, "name": created.name},
        )

    @router.post("/workspaces")
    async def create_workspace(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_CREATE_WORKSPACE,
            version=CAP_VERSION,
            payload=CreateWorkspaceCommand(name=str(body.get("name", ""))),
            resource=_org_resource(context),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "organization.invalid", str(exc), context.correlation_id)
        created: CreateWorkspaceResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=201,
            content={
                "workspace_id": created.workspace_id,
                "organization_id": created.organization_id,
            },
        )

    @router.post("/memberships")
    async def add_membership(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        roles = body.get("roles") or []
        command = Command(
            capability=CAP_ADD_MEMBERSHIP,
            version=CAP_VERSION,
            payload=AddMembershipCommand(
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
        added: AddMembershipResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=201,
            content={
                "membership_id": added.membership_id,
                "organization_id": added.organization_id,
                "subject_id": added.subject_id,
                "roles": list(added.roles),
            },
        )

    @router.get("/memberships")
    def list_memberships(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_LIST_MEMBERSHIPS,
            version=CAP_VERSION,
            parameters=ListMembershipsQuery(),
            resource=_org_resource(context),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        listed: ListMembershipsResult = result.value  # type: ignore[assignment]
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

    return router
