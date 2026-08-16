"""``/analytics`` FastAPI router (docs/17, FR-ANL-001..007).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch the
analytics capabilities on the bus, which authorize deny-by-default before the capability runs.
Policy denials surface as ``403 application/problem+json``; typed domain rejections (purpose-less
type,
malformed event, missing consent) as ``422``. No business logic lives here.
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
    CAP_CATALOG_REGISTER,
    CAP_EVENT_INGEST,
    CAP_GA4_IMPORT,
    CAP_IDENTITY_STITCH,
    CAP_REPORT_REACH,
    CAP_VERSION,
    ImportGa4Command,
    IngestEventCommand,
    RegisterEventDefinitionCommand,
    ReportReachQuery,
    StitchIdentityCommand,
)
from ..domain.errors import AnalyticsError
from ..domain.model import RES_ANALYTICS

_STATE_KEY = "northstar_analytics_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class AnalyticsApiDependencies:
    """Collaborators the ``/analytics`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_analytics_dependencies(app_state: object, deps: AnalyticsApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> AnalyticsApiDependencies:
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


def _analytics_resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_ANALYTICS, id=context.tenant_scope or "-")


def create_analytics_router() -> APIRouter:
    """Build the ``/analytics`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/analytics", tags=["analytics"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch_command(
        request: Request, context: RequestContext, cap: str, payload: object
    ) -> object:
        command = Command(
            capability=cap,
            version=CAP_VERSION,
            payload=payload,
            resource=_analytics_resource(context),
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/catalog")
    async def register_definition(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_CATALOG_REGISTER,
                RegisterEventDefinitionCommand(definition=dict(body)),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AnalyticsError, KernelError) as exc:
            return _problem(422, "analytics.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "event_name": result.value.event_name,
                "version": result.value.version,
                "purpose": result.value.purpose,
                "consent_category": result.value.consent_category,
            },
        )

    @router.post("/events")
    async def ingest_event(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_EVENT_INGEST,
                IngestEventCommand(
                    event_name=str(body.get("event_name", "")),
                    event_version=int(body.get("event_version", 0)),
                    actor_type=str(body.get("actor_type", "user")),
                    actor_id=str(body.get("actor_id", "")),
                    properties=dict(body.get("properties", {}) or {}),
                    anonymous_id=body.get("anonymous_id"),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AnalyticsError, KernelError) as exc:
            return _problem(422, "analytics.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "event_id": result.value.event_id,
                "event_name": result.value.event_name,
                "accepted": result.value.accepted,
                "authoritative": result.value.authoritative,
                "source": result.value.source,
            },
        )

    @router.post("/identity/stitch")
    async def stitch_identity(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_IDENTITY_STITCH,
                StitchIdentityCommand(
                    anonymous_id=str(body.get("anonymous_id", "")),
                    user_id=str(body.get("user_id", "")),
                    consent_categories=tuple(
                        str(c) for c in (body.get("consent_categories") or ())
                    ),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AnalyticsError, KernelError) as exc:
            return _problem(422, "analytics.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "anonymous_id": result.value.anonymous_id,
                "user_id": result.value.user_id,
                "linked": result.value.linked,
                "reason": result.value.reason,
            },
        )

    @router.get("/reach")
    def report_reach(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        event_name = request.query_params.get("event_name", "")
        content_property = request.query_params.get("content_property", "content_id")
        query = Query(
            capability=CAP_REPORT_REACH,
            version=CAP_VERSION,
            parameters=ReportReachQuery(event_name=event_name, content_property=content_property),
            resource=_analytics_resource(context),
        )
        try:
            result = _deps(request).query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AnalyticsError, KernelError) as exc:
            return _problem(422, "analytics.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "event_name": value.event_name,
                "total_events": value.total_events,
                "source": value.source,
                "authoritative": value.authoritative,
                "entries": [
                    {"content_id": e.content_id, "reach": e.reach, "occurrences": e.occurrences}
                    for e in value.entries
                ],
            },
        )

    @router.post("/ga4/import")
    async def import_ga4(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_GA4_IMPORT,
                ImportGa4Command(
                    northstar_event=str(body.get("northstar_event", "")),
                    ga4_event=str(body.get("ga4_event", "")),
                    metric_name=str(body.get("metric_name", "")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AnalyticsError, KernelError) as exc:
            return _problem(422, "analytics.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "metric_name": value.metric_name,
                "value": value.value,
                "source": value.source,
                "authoritative": value.authoritative,
                "as_of": value.as_of,
                "retrieved_at": value.retrieved_at,
                "mapping": value.mapping,
            },
        )

    return router
