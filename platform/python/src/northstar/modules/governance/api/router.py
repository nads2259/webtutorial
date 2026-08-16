"""``/governance`` FastAPI router (FR-GOV-001/002/003, EVAL-GOV-001/002).

A thin inbound adapter over the kernel command/query buses (LAW-04) — the governance surface is
exposed ONLY through capabilities, never a direct DB path (FR-GOV-003). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). Authorization denials (only an authorized approver may grant/revoke) surface as
``403 application/problem+json``; typed domain errors as ``422``. No business logic lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus

from ..application.capabilities import (
    CAP_EVALUATE_EXCEPTION,
    CAP_GRANT_EXCEPTION,
    CAP_RECORD_DECISION,
    CAP_REVOKE_EXCEPTION,
    CAP_SUPERSEDE_DECISION,
    CAP_VERSION,
    EvaluateExceptionQuery,
    GrantExceptionCommand,
    RecordDecisionCommand,
    RevokeExceptionCommand,
    SupersedeDecisionCommand,
)
from ..domain.model import RES_GOVERNANCE_DECISION, RES_GOVERNANCE_EXCEPTION

_STATE_KEY = "northstar_governance_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class GovernanceApiDependencies:
    """Collaborators the ``/governance`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_governance_dependencies(app_state: object, deps: GovernanceApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> GovernanceApiDependencies:
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


def _decision_resource(resource_id: str) -> ResourceRef:
    return ResourceRef(type=RES_GOVERNANCE_DECISION, id=resource_id or "-")


def _exception_resource(resource_id: str) -> ResourceRef:
    return ResourceRef(type=RES_GOVERNANCE_EXCEPTION, id=resource_id or "-")


def _tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def create_governance_router() -> APIRouter:
    """Build the ``/governance`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/governance", tags=["governance"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch_command(request: Request, command: Command) -> JSONResponse | object:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            return _deps(request).command_bus.dispatch(command, context).value
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "governance.invalid", str(exc), context.correlation_id)

    @router.post("/decisions")
    async def record_decision(request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_RECORD_DECISION,
                version=CAP_VERSION,
                payload=RecordDecisionCommand(
                    title=str(body.get("title", "")),
                    rationale=str(body.get("rationale", "")),
                    status=str(body.get("status", "accepted_baseline")),
                    controls=_tuple(body.get("controls")),
                    requirements=_tuple(body.get("requirements")),
                    gates=_tuple(body.get("gates")),
                ),
                resource=_decision_resource("-"),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=201, content=_decision_body(outcome))

    @router.post("/decisions/{decision_id}/supersede")
    async def supersede_decision(decision_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_SUPERSEDE_DECISION,
                version=CAP_VERSION,
                payload=SupersedeDecisionCommand(
                    prior_decision_id=decision_id,
                    title=str(body.get("title", "")),
                    rationale=str(body.get("rationale", "")),
                    status=str(body.get("status", "accepted_baseline")),
                    controls=_tuple(body.get("controls")),
                    requirements=_tuple(body.get("requirements")),
                    gates=_tuple(body.get("gates")),
                ),
                resource=_decision_resource(decision_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=201, content=_decision_body(outcome))

    @router.post("/exceptions")
    async def grant_exception(request: Request) -> JSONResponse:
        body = await _body(request)
        expiry = _parse_expiry(body.get("expiry"))
        if expiry is None:
            return _problem(
                422,
                "governance.exception.expiry_required",
                "a control exception requires an explicit ISO-8601 expiry",
                "-",
            )
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_GRANT_EXCEPTION,
                version=CAP_VERSION,
                payload=GrantExceptionCommand(
                    control=str(body.get("control", "")),
                    subject=str(body.get("subject", "")),
                    expiry=expiry,
                    rationale=str(body.get("rationale", "")),
                ),
                resource=_exception_resource(str(body.get("control", ""))),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=201, content=_exception_body(outcome))

    @router.post("/exceptions/{exception_id}/revoke")
    async def revoke_exception(exception_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_REVOKE_EXCEPTION,
                version=CAP_VERSION,
                payload=RevokeExceptionCommand(
                    exception_id=exception_id, reason=body.get("reason")
                ),
                resource=_exception_resource(exception_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(status_code=200, content=_exception_body(outcome))

    @router.get("/exceptions/evaluate")
    def evaluate_exception(control: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_EVALUATE_EXCEPTION,
            version=CAP_VERSION,
            parameters=EvaluateExceptionQuery(control=control),
            resource=_exception_resource(control),
        )
        try:
            result = _deps(request).query_bus.dispatch(query, context).value
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "governance.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "control": result.control,
                "honored": result.honored,
                "evaluated_at": result.evaluated_at.isoformat(),
                "active_exception_id": result.active_exception_id,
                "expired_exception_ids": list(result.expired_exception_ids),
            },
        )

    return router


def _decision_body(outcome: object) -> dict:
    return {
        "decision_id": outcome.decision_id,
        "status": outcome.status,
        "title": outcome.title,
        "controls": list(outcome.controls),
        "requirements": list(outcome.requirements),
        "gates": list(outcome.gates),
        "supersedes": outcome.supersedes,
    }


def _exception_body(outcome: object) -> dict:
    return {
        "exception_id": outcome.exception_id,
        "control": outcome.control,
        "subject": outcome.subject,
        "status": outcome.status,
        "expiry": outcome.expiry.isoformat(),
        "approver_id": outcome.approver_id,
        "active": outcome.active,
    }
