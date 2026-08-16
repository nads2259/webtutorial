"""``/privacy`` FastAPI router (EVAL-PRIV-001/002/003, EVAL-DATA-009).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope and the acting subject — is resolved by an
injected authenticator (server-side session), NEVER from the request body or a client header
(rule 50). Routes dispatch the privacy capabilities on the bus, which authorize deny-by-default
before the capability runs. Policy denials surface as ``403 application/problem+json``; a DSAR by a
non-subject or a non-zero deletion residue surface as ``422``/``403`` typed problems. No business
logic lives here.
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
    CAP_CATALOG_INSPECT,
    CAP_CATALOG_REGISTER,
    CAP_CONSENT_HISTORY,
    CAP_CONSENT_RECORD,
    CAP_RETENTION_SWEEP,
    CAP_RIGHTS_ACCESS,
    CAP_RIGHTS_ERASE,
    CAP_RIGHTS_EXPORT,
    CAP_VERSION,
    AccessCommand,
    ConsentHistoryQuery,
    EraseCommand,
    ExportCommand,
    InspectCatalogQuery,
    RecordConsentCommand,
    RegisterFieldCommand,
    SweepCommand,
)
from ..domain.errors import PrivacyError, UnauthorizedDataSubject
from ..domain.model import RES_PRIVACY

_STATE_KEY = "northstar_privacy_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class PrivacyApiDependencies:
    """Collaborators the ``/privacy`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_privacy_dependencies(app_state: object, deps: PrivacyApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> PrivacyApiDependencies:
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
    return ResourceRef(type=RES_PRIVACY, id=context.tenant_scope or "-")


def create_privacy_router() -> APIRouter:
    """Build the ``/privacy`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/privacy", tags=["privacy"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _command(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap, version=CAP_VERSION, payload=payload, resource=_resource(context)
        )
        return _deps(request).command_bus.dispatch(command, context)

    def _query(request: Request, context: RequestContext, cap: str, params: object) -> object:
        query = Query(
            capability=cap, version=CAP_VERSION, parameters=params, resource=_resource(context)
        )
        return _deps(request).query_bus.dispatch(query, context)

    def _run(
        context: RequestContext | None, fn: Callable[[], object]
    ) -> tuple[object, JSONResponse | None]:
        if context is None:
            return None, _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            return fn().value, None  # type: ignore[union-attr]
        except (PolicyDenied, UnauthorizedDataSubject):
            return None, _problem(
                403, "authorization.denied", "Access denied", context.correlation_id
            )
        except (PrivacyError, KernelError) as exc:
            return None, _problem(422, "privacy.rejected", str(exc), context.correlation_id)

    @router.post("/catalog/fields")
    async def register_field(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = RegisterFieldCommand(
            field_id=str(body.get("field_id", "")),
            module_id=str(body.get("module_id", "")),
            name=str(body.get("name", "")),
            purpose=str(body.get("purpose", "")),
            lawful_basis=str(body.get("lawful_basis", "")),
            data_class=str(body.get("data_class", "")),
            retention_days=int(body.get("retention_days", 0)),
            description=body.get("description"),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_CATALOG_REGISTER, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=201,
            content={
                "field_id": value.field_id,
                "module_id": value.module_id,
                "data_class": value.data_class,
                "retention_days": value.retention_days,
                "purpose": value.purpose,
                "lawful_basis": value.lawful_basis,
            },
        )

    @router.get("/catalog/fields")
    async def inspect_catalog(request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context, lambda: _query(request, context, CAP_CATALOG_INSPECT, InspectCatalogQuery())
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200, content={"fields": [_field_body(f) for f in value.fields]}
        )

    @router.post("/consent")
    async def record_consent(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = RecordConsentCommand(
            purpose=str(body.get("purpose", "")),
            category=str(body.get("category", "")),
            state=str(body.get("state", "granted")),
            lawful_basis=str(body.get("lawful_basis", "consent")),
            subject_id=body.get("subject_id"),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_CONSENT_RECORD, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(status_code=201, content=_consent_body(value))

    @router.get("/consent/history")
    async def consent_history(request: Request) -> JSONResponse:
        context = _auth(request)
        purpose = request.query_params.get("purpose", "")
        subject_id = request.query_params.get("subject_id")
        value, problem = _run(
            context,
            lambda: _query(
                request,
                context,
                CAP_CONSENT_HISTORY,
                ConsentHistoryQuery(purpose=purpose, subject_id=subject_id),
            ),
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "subject_id": value.subject_id,
                "purpose": value.purpose,
                "versions": [_consent_body(v) for v in value.versions],
            },
        )

    @router.post("/rights/access")
    async def rights_access(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = AccessCommand(subject_id=body.get("subject_id"))
        value, problem = _run(
            context, lambda: _command(request, context, CAP_RIGHTS_ACCESS, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "request_id": value.request_id,
                "subject_id": value.subject_id,
                "inventory": [
                    {"store_id": i.store_id, "item_count": i.item_count} for i in value.inventory
                ],
                "fields": [_field_body(f) for f in value.fields],
            },
        )

    @router.post("/rights/export")
    async def rights_export(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = ExportCommand(subject_id=body.get("subject_id"))
        value, problem = _run(
            context, lambda: _command(request, context, CAP_RIGHTS_EXPORT, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "request_id": value.request_id,
                "subject_id": value.subject_id,
                "store_ids": list(value.store_ids),
                "bundle": value.bundle,
            },
        )

    @router.post("/rights/erase")
    async def rights_erase(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = EraseCommand(subject_id=body.get("subject_id"))
        value, problem = _run(
            context, lambda: _command(request, context, CAP_RIGHTS_ERASE, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "request_id": value.request_id,
                "subject_id": value.subject_id,
                "erased_by_store": value.erased_by_store,
                "deletion_residue": value.deletion_residue,
            },
        )

    @router.post("/retention/sweep")
    async def retention_sweep(request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context, lambda: _command(request, context, CAP_RETENTION_SWEEP, SweepCommand())
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "swept_at": value.swept_at,
                "purged_by_store": value.purged_by_store,
                "total_purged": value.total_purged,
            },
        )

    return router


def _field_body(value: object) -> dict[str, object]:
    return {
        "field_id": value.field_id,
        "module_id": value.module_id,
        "name": value.name,
        "purpose": value.purpose,
        "lawful_basis": value.lawful_basis,
        "data_class": value.data_class,
        "retention_days": value.retention_days,
    }


def _consent_body(value: object) -> dict[str, object]:
    return {
        "record_id": value.record_id,
        "subject_id": value.subject_id,
        "purpose": value.purpose,
        "category": value.category,
        "state": value.state,
        "granted": value.granted,
        "version": value.version,
    }
