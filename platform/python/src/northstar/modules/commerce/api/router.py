"""``/commerce`` FastAPI router (docs/29, FR-COM-001..005).

A thin inbound adapter over the kernel command bus (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch
the commerce capabilities on the bus, which authorize deny-by-default before the capability runs.
Policy denials surface as ``403 application/problem+json``; a rejected payment callback (forged /
unsigned / tampered / replayed) surfaces as ``400``; other typed domain rejections as ``422``. No
business logic lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus

from ..application.capabilities import (
    CAP_AD_DISCLOSE,
    CAP_OFFER_PUBLISH,
    CAP_PAYMENT_CALLBACK,
    CAP_PURCHASE,
    CAP_REFUND_ISSUE,
    CAP_VERSION,
    DiscloseAdCommand,
    IssueRefundCommand,
    PaymentCallbackCommand,
    PublishOfferCommand,
    PurchaseCommand,
)
from ..domain.errors import CallbackRejected, CommerceError
from ..domain.model import RES_COMMERCE

_STATE_KEY = "northstar_commerce_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class CommerceApiDependencies:
    """Collaborators the ``/commerce`` router needs, injected at the composition root."""

    command_bus: CommandBus
    authenticate: Authenticator


def bind_commerce_dependencies(app_state: object, deps: CommerceApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> CommerceApiDependencies:
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


def _commerce_resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_COMMERCE, id=context.tenant_scope or "-")


def create_commerce_router() -> APIRouter:
    """Build the ``/commerce`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/commerce", tags=["commerce"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap,
            version=CAP_VERSION,
            payload=payload,
            resource=_commerce_resource(context),
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/offers")
    async def publish_offer(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_OFFER_PUBLISH,
                PublishOfferCommand(
                    offer=dict(body.get("offer", {}) or {}),
                    product_name=body.get("product_name"),
                    product_kind=str(body.get("product_kind", "course")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (CommerceError, KernelError) as exc:
            return _problem(422, "commerce.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "offer_id": value.offer_id,
                "version": value.version,
                "status": value.status,
                "is_free": value.is_free,
                "offer": value.contract,
            },
        )

    @router.post("/purchases")
    async def purchase(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_PURCHASE,
                PurchaseCommand(
                    offer_id=str(body.get("offer_id", "")),
                    offer_version=str(body.get("offer_version", "")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (CommerceError, KernelError) as exc:
            return _problem(422, "commerce.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "purchase_id": value.purchase_id,
                "status": value.status,
                "fulfilled": value.fulfilled,
                "grant_ids": list(value.grant_ids),
            },
        )

    @router.post("/payments/callback")
    async def payment_callback(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_PAYMENT_CALLBACK,
                PaymentCallbackCommand(
                    event_id=str(body.get("event_id", "")),
                    event_type=str(body.get("event_type", "")),
                    provider=str(body.get("provider", "")),
                    purchase_id=str(body.get("purchase_id", "")),
                    amount_minor=int(body.get("amount_minor", 0)),
                    currency=str(body.get("currency", "")),
                    signature=str(body.get("signature", "")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except CallbackRejected as exc:
            # Fail closed: a forged/unsigned/tampered/replayed callback never mutates state.
            return _problem(400, "commerce.callback.rejected", str(exc), context.correlation_id)
        except (CommerceError, KernelError) as exc:
            return _problem(422, "commerce.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "accepted": value.accepted,
                "purchase_id": value.purchase_id,
                "status": value.status,
                "grant_ids": list(value.grant_ids),
                "replayed": value.replayed,
            },
        )

    @router.post("/refunds")
    async def issue_refund(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_REFUND_ISSUE,
                IssueRefundCommand(purchase_id=str(body.get("purchase_id", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (CommerceError, KernelError) as exc:
            return _problem(422, "commerce.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "refund_id": value.refund_id,
                "purchase_id": value.purchase_id,
                "status": value.status,
                "revoked_grant_ids": list(value.revoked_grant_ids),
                "already_refunded": value.already_refunded,
            },
        )

    @router.post("/ads")
    async def disclose_ad(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_AD_DISCLOSE,
                DiscloseAdCommand(
                    placement_id=str(body.get("placement_id", "")),
                    kind=str(body.get("kind", "")),
                    disclosure_label=str(body.get("disclosure_label", "")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (CommerceError, KernelError) as exc:
            return _problem(422, "commerce.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "placement_id": value.placement_id,
                "kind": value.kind,
                "disclosed": value.disclosed,
                "is_advertising": value.is_advertising,
                "disclosure_label": value.disclosure_label,
            },
        )

    return router
