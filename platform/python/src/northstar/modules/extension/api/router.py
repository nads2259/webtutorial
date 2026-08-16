"""``/extensions`` FastAPI router (docs/14, FR-EXT-001..008).

A thin inbound adapter over the kernel command bus (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch the
extension capabilities on the bus, which authorize deny-by-default before the capability runs.
Policy denials surface as ``403 application/problem+json``; typed domain errors as ``422``. No
business logic here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus

from ..application.capabilities import (
    CAP_APPLY_THEME,
    CAP_DISABLE,
    CAP_INSTALL,
    CAP_PUBLISH_CATALOG,
    CAP_UNINSTALL,
    CAP_UPGRADE,
    CAP_VERSION,
    RES_EXTENSION,
    ApplyThemeCommand,
    DisableExtensionCommand,
    InstallExtensionCommand,
    PublishCatalogCommand,
    UninstallExtensionCommand,
    UpgradeExtensionCommand,
)
from ..domain.errors import ExtensionError

_STATE_KEY = "northstar_extension_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class ExtensionApiDependencies:
    """Collaborators the ``/extensions`` router needs, injected at the composition root."""

    command_bus: CommandBus
    authenticate: Authenticator


def bind_extension_dependencies(app_state: object, deps: ExtensionApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> ExtensionApiDependencies:
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
    return ResourceRef(type=RES_EXTENSION, id=context.tenant_scope or "-")


def _artifact_bytes(body: dict) -> bytes:
    raw = body.get("artifact")
    if isinstance(raw, str) and raw:
        try:
            return base64.b64decode(raw)
        except (ValueError, binascii.Error):
            return raw.encode("utf-8")
    return b""


def create_extension_router() -> APIRouter:
    """Build the ``/extensions`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/extensions", tags=["extensions"])

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

    @router.post("/install")
    async def install(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_INSTALL,
                InstallExtensionCommand(
                    manifest=dict(body.get("manifest", {}) or {}),
                    artifact=_artifact_bytes(body),
                    block_schema=body.get("block_schema"),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "extension_id": result.value.extension_id,
                "version": result.value.version,
                "granted_trust_tier": result.value.granted_trust_tier,
                "state": result.value.state,
            },
        )

    @router.post("/upgrade")
    async def upgrade(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_UPGRADE,
                UpgradeExtensionCommand(
                    manifest=dict(body.get("manifest", {}) or {}),
                    artifact=_artifact_bytes(body),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "extension_id": result.value.extension_id,
                "version": result.value.version,
                "granted_trust_tier": result.value.granted_trust_tier,
                "state": result.value.state,
            },
        )

    @router.post("/disable")
    async def disable(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_DISABLE,
                DisableExtensionCommand(extension_id=str(body.get("extension_id", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "extension_id": result.value.extension_id,
                "state": result.value.state,
                "revoked_actions": list(result.value.revoked_actions),
            },
        )

    @router.post("/uninstall")
    async def uninstall(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_UNINSTALL,
                UninstallExtensionCommand(extension_id=str(body.get("extension_id", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "extension_id": result.value.extension_id,
                "data_policy": result.value.data_policy,
                "revoked_actions": list(result.value.revoked_actions),
            },
        )

    @router.post("/themes/apply")
    async def apply_theme(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_APPLY_THEME,
                ApplyThemeCommand(
                    theme_manifest=dict(body.get("theme_manifest", {}) or {}),
                    theme_tokens=dict(body.get("theme_tokens", {}) or {}),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "theme_id": result.value.theme_id,
                "version": result.value.version,
                "slots": list(result.value.slots),
                "modes": list(result.value.modes),
            },
        )

    @router.post("/catalog/publish")
    async def publish_catalog(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_PUBLISH_CATALOG,
                PublishCatalogCommand(manifest=dict(body.get("manifest", {}) or {})),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ExtensionError, KernelError) as exc:
            return _problem(422, "extension.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "extension_id": result.value.extension_id,
                "version": result.value.version,
                "publisher_id": result.value.publisher_id,
                "verified": result.value.verified,
            },
        )

    return router
