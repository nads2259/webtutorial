"""``/messaging`` FastAPI router (docs/16, FR-MSG-001..007).

A thin inbound adapter over the kernel command bus (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch
the messaging capabilities on the bus, which authorize deny-by-default before the capability runs.
Policy
denials surface as ``403 application/problem+json``; typed domain errors (unsafe segment, immutable
template, suppressed recipient) as ``422``. No business logic lives here.
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
    CAP_CAMPAIGN_CREATE,
    CAP_CAMPAIGN_SCHEDULE,
    CAP_CAMPAIGN_SEND,
    CAP_CONSENT_UNSUBSCRIBE,
    CAP_TEMPLATE_PUBLISH,
    CAP_VERSION,
    CreateCampaignCommand,
    PublishTemplateVersionCommand,
    ScheduleCampaignCommand,
    SendCampaignCommand,
    UnsubscribeCommand,
)
from ..application.transactional import (
    CAP_OUTBOX_LIST,
    CAP_TEMPLATE_GET,
    CAP_TEMPLATE_LIST,
    GetTemplateQuery,
    ListOutboxQuery,
    ListTemplatesQuery,
)
from ..domain.errors import MessagingError
from ..domain.model import RES_CAMPAIGN, Recipient

_STATE_KEY = "northstar_messaging_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class MessagingApiDependencies:
    """Collaborators the ``/messaging`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator
    admin_lookup: Callable[[str], bool] = lambda _subject_id: False


def bind_messaging_dependencies(app_state: object, deps: MessagingApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> MessagingApiDependencies:
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


def _campaign_resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_CAMPAIGN, id=context.tenant_scope or "-")


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _recipients_from_body(body: dict) -> tuple[Recipient, ...]:
    recipients: list[Recipient] = []
    for raw in body.get("recipients") or ():
        if not isinstance(raw, dict):
            continue
        recipients.append(
            Recipient(
                recipient_id=str(raw.get("recipient_id", "")),
                address=str(raw.get("address", "")),
                timezone=str(raw.get("timezone", "UTC")),
                attributes={k: str(v) for k, v in dict(raw.get("attributes", {}) or {}).items()},
                variables={k: str(v) for k, v in dict(raw.get("variables", {}) or {}).items()},
            )
        )
    return tuple(recipients)


def create_messaging_router() -> APIRouter:
    """Build the ``/messaging`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/messaging", tags=["messaging"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _admin(request: Request, context: RequestContext) -> JSONResponse | None:
        if not _deps(request).admin_lookup(context.actor.id):
            return _problem(
                403, "authorization.denied", "Admin access required", context.correlation_id
            )
        return None

    def _dispatch(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap,
            version=CAP_VERSION,
            payload=payload,
            resource=_campaign_resource(context),
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/templates/publish")
    async def publish_template(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        denied = _admin(request, context)
        if denied is not None:
            return denied
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_TEMPLATE_PUBLISH,
                PublishTemplateVersionCommand(
                    template_id=str(body.get("template_id", "")),
                    version=int(body.get("version", 0)),
                    subject=str(body.get("subject", "")),
                    html_body=str(body.get("html_body", "")),
                    text_body=str(body.get("text_body", "")),
                    required_variables=tuple(body.get("required_variables") or ()),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(422, "messaging.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "template_id": result.value.template_id,
                "version": result.value.version,
                "content_hash": result.value.content_hash,
            },
        )

    @router.post("/campaigns")
    async def create_campaign(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_CAMPAIGN_CREATE,
                CreateCampaignCommand(
                    name=str(body.get("name", "")),
                    message_class=str(body.get("message_class", "")),
                    template_id=str(body.get("template_id", "")),
                    template_version=int(body.get("template_version", 0)),
                    channel=str(body.get("channel", "email")),
                    purpose=str(body.get("purpose", "marketing")),
                    segment_specs=tuple(body.get("segment") or ()),
                    tracking=body.get("tracking"),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(422, "messaging.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "campaign_id": result.value.campaign_id,
                "message_class": result.value.message_class,
                "template_id": result.value.template_id,
                "template_version": result.value.template_version,
                "status": result.value.status,
                "open_tracking": result.value.open_tracking,
                "click_tracking": result.value.click_tracking,
            },
        )

    @router.post("/campaigns/{campaign_id}/schedule")
    async def schedule_campaign(campaign_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_CAMPAIGN_SCHEDULE,
                ScheduleCampaignCommand(
                    campaign_id=campaign_id,
                    schedule=dict(body.get("schedule", {}) or {}),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(422, "messaging.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "campaign_id": result.value.campaign_id,
                "status": result.value.status,
                "schedule_kind": result.value.schedule_kind,
            },
        )

    @router.post("/campaigns/{campaign_id}/send")
    async def send_campaign(campaign_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_CAMPAIGN_SEND,
                SendCampaignCommand(
                    campaign_id=campaign_id,
                    recipients=_recipients_from_body(body),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(422, "messaging.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "campaign_id": value.campaign_id,
                "submitted": value.submitted,
                "deduplicated": value.deduplicated,
                "segment_excluded": value.segment_excluded,
                "suppressed_excluded": value.suppressed_excluded,
                "consent_excluded": value.consent_excluded,
                "suppression_leak": value.suppression_leak,
            },
        )

    @router.get("/templates")
    def list_templates(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        denied = _admin(request, context)
        if denied is not None:
            return denied
        try:
            result = _deps(request).query_bus.dispatch(
                Query(capability=CAP_TEMPLATE_LIST, version=CAP_VERSION, parameters=ListTemplatesQuery()),
                context,
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "templates": [
                    {
                        "template_id": t.template_id,
                        "version": t.version,
                        "subject": t.subject,
                        "required_variables": list(t.required_variables),
                    }
                    for t in result.value.templates
                ]
            },
        )

    @router.get("/templates/{template_id}")
    def get_template(template_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        denied = _admin(request, context)
        if denied is not None:
            return denied
        try:
            result = _deps(request).query_bus.dispatch(
                Query(
                    capability=CAP_TEMPLATE_GET,
                    version=CAP_VERSION,
                    parameters=GetTemplateQuery(template_id=template_id),
                ),
                context,
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(404, "messaging.template.not-found", str(exc), context.correlation_id)
        t = result.value
        return JSONResponse(
            status_code=200,
            content={
                "template_id": t.template_id,
                "version": t.version,
                "subject": t.subject,
                "html_body": t.html_body,
                "text_body": t.text_body,
                "required_variables": list(t.required_variables),
            },
        )

    @router.get("/outbox")
    def list_outbox(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        denied = _admin(request, context)
        if denied is not None:
            return denied
        params = request.query_params
        try:
            result = _deps(request).query_bus.dispatch(
                Query(
                    capability=CAP_OUTBOX_LIST,
                    version=CAP_VERSION,
                    parameters=ListOutboxQuery(
                        limit=min(max(_int(params.get("limit"), 25), 1), 100),
                        offset=max(_int(params.get("offset"), 0), 0),
                        status=params.get("status") or None,
                        q=params.get("q") or None,
                        created_after=params.get("created_after") or None,
                        created_before=params.get("created_before") or None,
                    ),
                ),
                context,
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "messages": [
                    {
                        "message_id": m.message_id,
                        "to_email": m.to_email,
                        "template_id": m.template_id,
                        "subject": m.subject,
                        "html_body": m.html_body,
                        "status": m.status,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in view.messages
                ],
                "total": view.total if view.total is not None else len(view.messages),
            },
        )

    @router.post("/consent/unsubscribe")
    async def unsubscribe(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_CONSENT_UNSUBSCRIBE,
                UnsubscribeCommand(
                    recipient_id=str(body.get("recipient_id", "")),
                    channel=str(body.get("channel", "email")),
                    purpose=str(body.get("purpose", "marketing")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (MessagingError, KernelError) as exc:
            return _problem(422, "messaging.rejected", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "recipient_id": result.value.recipient_id,
                "suppressed": result.value.suppressed,
                "reason": result.value.reason,
            },
        )

    return router
