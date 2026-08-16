"""``/simulations`` FastAPI router (docs/15, FR-SIM-001..008).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch the
simulation capabilities on the bus, which authorize deny-by-default before the capability runs.
Policy denials surface as ``403 application/problem+json``; typed domain errors as ``422``.
No business logic here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, QueryBus

from ..application.capabilities import (
    CAP_COACH,
    CAP_DEFINE,
    CAP_ISSUE_LEASE,
    CAP_PUBLISH,
    CAP_RUN,
    CAP_SET_TIER,
    CAP_VERSION,
    RES_SIMULATION,
    CoachCommand,
    DefineSimulationCommand,
    IssueLeaseCommand,
    PublishSimulationCommand,
    RunSimulationCommand,
    SetTrustTierCommand,
)
from ..domain.errors import SimulationError

_STATE_KEY = "northstar_simulation_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class SimulationApiDependencies:
    """Collaborators the ``/simulations`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_simulation_dependencies(app_state: object, deps: SimulationApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> SimulationApiDependencies:
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


def _resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_SIMULATION, id=context.tenant_scope or "-")


def create_simulation_router() -> APIRouter:
    """Build the ``/simulations`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/simulations", tags=["simulations"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap, version=CAP_VERSION, payload=payload, resource=_resource(context)
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/definitions")
    async def define(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_DEFINE,
                DefineSimulationCommand(
                    definition=dict(body.get("definition", {}) or {}),
                    scoring_key=str(body.get("scoring_key", "")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "simulation_id": result.value.simulation_id,
                "version": result.value.version,
                "content_hash": result.value.content_hash,
            },
        )

    @router.post("/definitions/{simulation_id}/publish")
    async def publish(request: Request, simulation_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_PUBLISH,
                PublishSimulationCommand(
                    simulation_id=simulation_id, version=str(body.get("version", ""))
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={"content_hash": result.value.content_hash, "status": result.value.status},
        )

    @router.post("/trust-tiers")
    async def set_tier(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_SET_TIER,
                SetTrustTierCommand(
                    tier=str(body.get("tier", "")),
                    approved=bool(body.get("approved", False)),
                    max_quota=body.get("max_quota"),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200, content={"tier": result.value.tier, "approved": result.value.approved}
        )

    @router.post("/leases")
    async def issue_lease(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_ISSUE_LEASE,
                IssueLeaseCommand(
                    simulation_id=str(body.get("simulation_id", "")),
                    version=str(body.get("version", "")),
                    tier=str(body.get("tier", "")),
                    ttl_seconds=int(body.get("ttl_seconds", 300)),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "lease_id": result.value.lease_id,
                "token": result.value.token,
                "tier": result.value.tier,
                "expires_at": result.value.expires_at,
            },
        )

    @router.post("/runs")
    async def run(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_RUN,
                RunSimulationCommand(
                    token=str(body.get("token", "")),
                    inputs=dict(body.get("inputs", {}) or {}),
                    seed=str(body.get("seed", "0")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "run_id": result.value.run_id,
                "status": result.value.status,
                "termination_reason": result.value.termination_reason,
                "evidence_head_hash": result.value.evidence_head_hash,
                "evidence_verified": result.value.evidence_verified,
                "score_value": result.value.score_value,
            },
        )

    @router.post("/coach")
    async def coach(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_COACH,
                CoachCommand(
                    simulation_id=str(body.get("simulation_id", "")),
                    version=str(body.get("version", "")),
                    question=str(body.get("question", "")),
                    package_id=str(body.get("package_id", "")),
                    package_version=str(body.get("package_version", "1.0.0")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SimulationError, KernelError) as exc:
            return _problem(422, "simulation.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "hint": result.value.hint,
                "refused": result.value.refused,
                "disclosed_scoring_key": result.value.disclosed_scoring_key,
                "trace_id": result.value.trace_id,
            },
        )

    return router
