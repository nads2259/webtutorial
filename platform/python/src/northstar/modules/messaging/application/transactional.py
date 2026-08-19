"""Transactional email: durable 1:1 send + admin-managed templates + outbox (LAW-04).

A lighter path than the campaign/consent machinery for service email (confirmation, password reset):
render the latest admin-published template version, record the rendered message to a durable outbox
(the "dev mailbox"), and deliver it (real SMTP when configured, otherwise recorded-only). Templates
are the existing IMMUTABLE versioned ``template_version`` rows; editing = publish version N+1.

Capabilities:

* ``messaging.transactional.send`` — render + record + deliver one transactional email.
* ``template.list`` (query) — latest version of every template (admin).
* ``template.get`` (query) — a specific template version (admin).
* ``messaging.outbox.list`` (query) — recent sent/recorded emails (admin outbox).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..domain.errors import TenantScopeMissing
from ..domain.model import TemplateVersion
from .ports import MessagingRepositoryPort

CAP_VERSION = "1.0.0"

CAP_TRANSACTIONAL_SEND = "messaging.transactional.send"
CAP_TEMPLATE_LIST = "template.list"
CAP_TEMPLATE_GET = "template.get"
CAP_OUTBOX_LIST = "messaging.outbox.list"

TRANSACTIONAL_CAPABILITIES: tuple[str, ...] = (
    CAP_TRANSACTIONAL_SEND,
    CAP_TEMPLATE_LIST,
    CAP_TEMPLATE_GET,
    CAP_OUTBOX_LIST,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class EmailStatus(StrEnum):
    RECORDED = "recorded"  # written to the durable mailbox; not delivered by a real provider (dev)
    SENT = "sent"  # accepted by a real provider (SMTP)
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    message_id: str
    organization_id: str
    to_email: str
    template_id: str | None
    subject: str
    html_body: str
    text_body: str
    status: EmailStatus
    created_at: datetime
    provider_message_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: EmailStatus
    provider_message_id: str | None = None
    error: str | None = None


@runtime_checkable
class EmailDeliveryPort(Protocol):
    """Delivers a rendered email. The dev adapter records-only; SMTP actually sends."""

    def deliver(
        self, *, to_email: str, subject: str, html_body: str, text_body: str
    ) -> DeliveryOutcome: ...


@runtime_checkable
class EmailOutboxStorePort(Protocol):
    """Durable, tenant-scoped store of every transactional email (the admin Outbox)."""

    def record(self, *, message: EmailMessage) -> None: ...

    def list_recent(
        self,
        *,
        organization_id: str,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[EmailMessage]: ...

    def count_recent(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        q: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int: ...


# Built-in fallback templates so auth email still works before an admin seeds/edits them.
_FALLBACK: dict[str, TemplateVersion] = {
    "account-confirmation": TemplateVersion(
        template_id="account-confirmation",
        version=1,
        subject="Confirm your Bestinfopages account",
        html_body=(
            "<p>Welcome to Bestinfopages!</p>"
            "<p>Confirm your email to activate your account:</p>"
            '<p><a href="{{link}}">Confirm my email</a></p>'
            "<p>If the link does not work, paste this into your browser:<br>{{link}}</p>"
        ),
        text_body="Welcome to Bestinfopages! Confirm your email: {{link}}",
        required_variables=("email", "link"),
    ),
    "password-reset": TemplateVersion(
        template_id="password-reset",
        version=1,
        subject="Reset your Bestinfopages password",
        html_body=(
            "<p>We received a request to reset your password.</p>"
            '<p><a href="{{link}}">Choose a new password</a></p>'
            "<p>If you did not request this, you can ignore this email.</p>"
            "<p>Link: {{link}}</p>"
        ),
        text_body="Reset your Bestinfopages password: {{link}}",
        required_variables=("email", "link"),
    ),
}


def default_templates() -> tuple[TemplateVersion, ...]:
    """The built-in transactional templates seeded on bootstrap (admins publish new versions)."""
    return tuple(_FALLBACK.values())


class TransactionalEmailService:
    """Renders the latest template, records it to the outbox, and delivers it."""

    def __init__(
        self,
        *,
        repository: MessagingRepositoryPort,
        outbox: EmailOutboxStorePort,
        delivery: EmailDeliveryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._outbox = outbox
        self._delivery = delivery
        self._clock = clock
        self._id = id_factory

    def send(
        self,
        *,
        organization_id: str,
        template_id: str,
        to_email: str,
        variables: Mapping[str, str],
    ) -> EmailMessage:
        template = self._repo.get_latest_template(
            organization_id=organization_id, template_id=template_id
        ) or _FALLBACK.get(template_id)
        if template is None:
            raise ValueError(f"unknown email template {template_id!r}")
        rendered = template.render(dict(variables))
        outcome: DeliveryOutcome
        try:
            outcome = self._delivery.deliver(
                to_email=to_email,
                subject=rendered.subject,
                html_body=rendered.html_body,
                text_body=rendered.text_body,
            )
        except Exception as exc:  # noqa: BLE001 - a provider failure is recorded, never lost
            outcome = DeliveryOutcome(status=EmailStatus.FAILED, error=str(exc)[:500])
        message = EmailMessage(
            message_id=self._id(),
            organization_id=organization_id,
            to_email=to_email,
            template_id=template_id,
            subject=rendered.subject,
            html_body=rendered.html_body,
            text_body=rendered.text_body,
            status=outcome.status,
            created_at=self._clock(),
            provider_message_id=outcome.provider_message_id,
            error=outcome.error,
        )
        self._outbox.record(message=message)
        return message


# --------------------------------------------------------------------------- capabilities


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    scope = getattr(getattr(invocation, "context", None), "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True, slots=True)
class SendTransactionalCommand:
    template_id: str
    to_email: str
    variables: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendTransactionalResult:
    message_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ListTemplatesQuery:
    pass


@dataclass(frozen=True, slots=True)
class TemplateView:
    template_id: str
    version: int
    subject: str
    html_body: str
    text_body: str
    required_variables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplatesView:
    templates: tuple[TemplateView, ...]


@dataclass(frozen=True, slots=True)
class GetTemplateQuery:
    template_id: str
    version: int | None = None


@dataclass(frozen=True, slots=True)
class ListOutboxQuery:
    limit: int = 25
    offset: int = 0
    status: str | None = None
    q: str | None = None
    created_after: str | None = None
    created_before: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxEntryView:
    message_id: str
    to_email: str
    template_id: str | None
    subject: str
    html_body: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxView:
    messages: tuple[OutboxEntryView, ...]
    total: int | None = None


def _template_view(t: TemplateVersion) -> TemplateView:
    return TemplateView(
        template_id=t.template_id,
        version=t.version,
        subject=t.subject,
        html_body=t.html_body,
        text_body=t.text_body,
        required_variables=tuple(t.required_variables),
    )


class SendTransactionalEmail:
    """``messaging.transactional.send`` — render + record + deliver one email."""

    def __init__(self, *, service: TransactionalEmailService) -> None:
        self._service = service

    def handle(self, request: object) -> SendTransactionalResult:
        command = _typed(request, SendTransactionalCommand)
        organization_id = _tenant(request)
        message = self._service.send(
            organization_id=organization_id,
            template_id=command.template_id,
            to_email=command.to_email,
            variables=command.variables,
        )
        return SendTransactionalResult(message_id=message.message_id, status=message.status.value)


class ListTemplates:
    """``template.list`` (query) — latest version of every template (admin)."""

    def __init__(self, *, repository: MessagingRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> TemplatesView:
        _typed(request, ListTemplatesQuery)
        organization_id = _tenant(request)
        stored = {
            t.template_id: t
            for t in self._repo.list_templates(organization_id=organization_id)
        }
        # Include built-in defaults that have not been overridden by a published version.
        for template_id, fallback in _FALLBACK.items():
            stored.setdefault(template_id, fallback)
        return TemplatesView(
            templates=tuple(_template_view(t) for t in sorted(stored.values(), key=lambda x: x.template_id))
        )


class GetTemplate:
    """``template.get`` (query) — a specific template version (admin)."""

    def __init__(self, *, repository: MessagingRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> TemplateView:
        query = _typed(request, GetTemplateQuery)
        organization_id = _tenant(request)
        if query.version is not None:
            template = self._repo.get_template_version(
                organization_id=organization_id,
                template_id=query.template_id,
                version=query.version,
            )
        else:
            template = self._repo.get_latest_template(
                organization_id=organization_id, template_id=query.template_id
            )
        template = template or _FALLBACK.get(query.template_id)
        if template is None:
            raise ValueError(f"unknown email template {query.template_id!r}")
        return _template_view(template)


class ListOutbox:
    """``messaging.outbox.list`` (query) — recent sent/recorded emails (admin outbox)."""

    def __init__(self, *, outbox: EmailOutboxStorePort) -> None:
        self._outbox = outbox

    def handle(self, request: object) -> OutboxView:
        query = _typed(request, ListOutboxQuery)
        organization_id = _tenant(request)
        status: str | None = None
        if query.status:
            try:
                status = EmailStatus(query.status).value
            except ValueError:
                return OutboxView(messages=(), total=0)
        needle = (query.q or "").strip() or None
        created_after = _parse_dt(query.created_after)
        created_before = _parse_dt(query.created_before)
        limit = max(1, min(query.limit, 100))
        offset = max(0, query.offset)
        rows = self._outbox.list_recent(
            organization_id=organization_id,
            limit=limit,
            offset=offset,
            status=status,
            q=needle,
            created_after=created_after,
            created_before=created_before,
        )
        total = self._outbox.count_recent(
            organization_id=organization_id,
            status=status,
            q=needle,
            created_after=created_after,
            created_before=created_before,
        )
        return OutboxView(
            messages=tuple(
                OutboxEntryView(
                    message_id=m.message_id,
                    to_email=m.to_email,
                    template_id=m.template_id,
                    subject=m.subject,
                    html_body=m.html_body,
                    status=m.status.value,
                    created_at=m.created_at,
                )
                for m in rows
            ),
            total=total,
        )
