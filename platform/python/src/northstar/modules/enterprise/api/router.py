"""``/enterprise`` FastAPI router (FR-IDN-006, FR-LRN-008; EVAL-IDN-005, EVAL-INT-001).

A thin inbound adapter over the kernel command/query buses (LAW-04) — the enterprise surface is
exposed ONLY through capabilities, never a direct DB path. The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). A rejected federation assertion / LTI launch and a missing consent surface as
``422 application/problem+json``; authorization denials as ``403``. No business logic lives here;
signature material stays on the wire objects passed straight into typed commands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, QueryBus

from ..application.capabilities import (
    CAP_FEDERATION_LOGIN,
    CAP_LTI_LAUNCH,
    CAP_SCIM_DEPROVISION,
    CAP_SCIM_PROVISION,
    CAP_VERSION,
    CAP_XAPI_EMIT,
    FederationLoginCommand,
    LtiLaunchCommand,
    ScimDeprovisionCommand,
    ScimProvisionCommand,
    XapiEmitCommand,
)
from ..domain.model import (
    RES_ENTERPRISE_FEDERATION,
    RES_ENTERPRISE_LTI,
    RES_ENTERPRISE_PROVISIONING,
    RES_ENTERPRISE_XAPI,
    FederationAssertion,
    LtiLaunch,
    ProvisioningResourceType,
)

_STATE_KEY = "northstar_enterprise_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class EnterpriseApiDependencies:
    """Collaborators the ``/enterprise`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_enterprise_dependencies(app_state: object, deps: EnterpriseApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> EnterpriseApiDependencies:
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


def _resource(resource_type: str, resource_id: str) -> ResourceRef:
    return ResourceRef(type=resource_type, id=resource_id or "-")


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def create_enterprise_router() -> APIRouter:
    """Build the ``/enterprise`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/enterprise", tags=["enterprise"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch(
        request: Request, command: Command, context: RequestContext
    ) -> JSONResponse | object:
        try:
            return _deps(request).command_bus.dispatch(command, context).value
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "enterprise.invalid", str(exc), context.correlation_id)

    @router.post("/federation/login")
    async def federation_login(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        issued_at = _parse_dt(body.get("issued_at"))
        expires_at = _parse_dt(body.get("expires_at"))
        if issued_at is None or expires_at is None:
            return _problem(
                422,
                "enterprise.federation.window",
                "issued_at and expires_at (ISO-8601) are required",
                context.correlation_id,
            )
        try:
            assertion = FederationAssertion(
                issuer=str(body.get("issuer", "")),
                subject=str(body.get("subject", "")),
                audience=str(body.get("audience", "")),
                issued_at=issued_at,
                expires_at=expires_at,
                signature=str(body.get("signature", "")),
                email=body.get("email"),
                display_name=body.get("display_name"),
            )
        except KernelError as exc:
            return _problem(422, "enterprise.invalid", str(exc), context.correlation_id)
        outcome = _dispatch(
            request,
            Command(
                capability=CAP_FEDERATION_LOGIN,
                version=CAP_VERSION,
                payload=FederationLoginCommand(assertion=assertion),
                resource=_resource(RES_ENTERPRISE_FEDERATION, assertion.issuer),
            ),
            context,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "subject_id": outcome.subject_id,
                "user_id": outcome.user_id,
                "issuer": outcome.issuer,
                "external_subject": outcome.external_subject,
                "provisioned": outcome.provisioned,
            },
        )

    @router.post("/scim/resources")
    async def scim_provision(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            resource_type = ProvisioningResourceType(str(body.get("resource_type", "user")))
        except ValueError:
            return _problem(
                422, "enterprise.scim.type", "unknown SCIM resource type", context.correlation_id
            )
        outcome = _dispatch(
            request,
            Command(
                capability=CAP_SCIM_PROVISION,
                version=CAP_VERSION,
                payload=ScimProvisionCommand(
                    external_id=str(body.get("external_id", "")),
                    resource_type=resource_type,
                    active=bool(body.get("active", True)),
                    email=body.get("email"),
                    display_name=body.get("display_name"),
                    members=_tuple(body.get("members")),
                ),
                resource=_resource(RES_ENTERPRISE_PROVISIONING, str(body.get("external_id", ""))),
            ),
            context,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=201, content=_provisioning_body(outcome))

    @router.post("/scim/resources/{external_id}/deactivate")
    async def scim_deprovision(external_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        outcome = _dispatch(
            request,
            Command(
                capability=CAP_SCIM_DEPROVISION,
                version=CAP_VERSION,
                payload=ScimDeprovisionCommand(external_id=external_id),
                resource=_resource(RES_ENTERPRISE_PROVISIONING, external_id),
            ),
            context,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=200, content=_provisioning_body(outcome))

    @router.post("/lti/launch")
    async def lti_launch(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        issued_at = _parse_dt(body.get("issued_at"))
        expires_at = _parse_dt(body.get("expires_at"))
        if issued_at is None or expires_at is None:
            return _problem(
                422,
                "enterprise.lti.window",
                "issued_at and expires_at (ISO-8601) are required",
                context.correlation_id,
            )
        try:
            launch = LtiLaunch(
                issuer=str(body.get("issuer", "")),
                deployment_id=str(body.get("deployment_id", "")),
                context_id=str(body.get("context_id", "")),
                resource_link_id=str(body.get("resource_link_id", "")),
                subject=str(body.get("subject", "")),
                issued_at=issued_at,
                expires_at=expires_at,
                signature=str(body.get("signature", "")),
                roles=_tuple(body.get("roles")),
            )
        except KernelError as exc:
            return _problem(422, "enterprise.invalid", str(exc), context.correlation_id)
        outcome = _dispatch(
            request,
            Command(
                capability=CAP_LTI_LAUNCH,
                version=CAP_VERSION,
                payload=LtiLaunchCommand(launch=launch),
                resource=_resource(RES_ENTERPRISE_LTI, launch.context_id),
            ),
            context,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "issuer": outcome.issuer,
                "context_id": outcome.context_id,
                "resource_link_id": outcome.resource_link_id,
                "subject": outcome.subject,
                "roles": list(outcome.roles),
            },
        )

    @router.post("/xapi/statements")
    async def xapi_emit(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        outcome = _dispatch(
            request,
            Command(
                capability=CAP_XAPI_EMIT,
                version=CAP_VERSION,
                payload=XapiEmitCommand(
                    subject_id=str(body.get("subject_id", "")),
                    course_id=str(body.get("course_id", "")),
                    course_title=str(body.get("course_title", "")),
                    completed=bool(body.get("completed", False)),
                    registration=body.get("registration"),
                ),
                resource=_resource(RES_ENTERPRISE_XAPI, str(body.get("subject_id", ""))),
            ),
            context,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "statement_id": outcome.statement_id,
                "stored": outcome.stored,
                "verb_id": outcome.verb_id,
                "object_id": outcome.object_id,
            },
        )

    return router


def _provisioning_body(outcome: object) -> dict:
    return {
        "record_id": outcome.record_id,
        "external_id": outcome.external_id,
        "resource_type": outcome.resource_type,
        "active": outcome.active,
        "subject_id": outcome.subject_id,
        "created": outcome.created,
        "sessions_invalidated": outcome.sessions_invalidated,
    }
