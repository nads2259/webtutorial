"""Messaging capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command bus, so each mutation is authorized deny-by-default
and audited (rule 50, LAW-14). Tenant scope + acting subject come from the authenticated
:class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on :mod:`.ports` and
the pure :mod:`..domain`.

The messaging invariants are enforced here by construction and are never weakened:

* ``template.publish`` writes an IMMUTABLE version; republishing a version is rejected (FR-MSG-002).
* ``campaign.create`` binds an EXACT existing template version and a safe segment (FR-MSG-002/003).
* ``campaign.schedule`` records a recipient-time-zone-aware schedule (FR-MSG-004).
* ``campaign.send`` ALWAYS applies consent + suppression to a marketing send — a suppressed /
  unsubscribed / non-consented recipient is NEVER submitted (suppression_leak == 0) — and submits to
  the provider IDEMPOTENTLY, so a re-submitted (campaign, recipient, key) never double-sends
  (FR-MSG-005/006).
* ``consent.unsubscribe`` suppresses a recipient immediately and idempotently (FR-MSG-005).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from ..domain.errors import (
    CampaignNotFound,
    TemplateVersionNotFound,
    TenantScopeMissing,
)
from ..domain.model import (
    RES_CAMPAIGN,
    Campaign,
    CampaignStatus,
    ConsentRecord,
    DeliveryReceipt,
    DeliveryStatus,
    MessageClass,
    Recipient,
    Schedule,
    Segment,
    SuppressionEntry,
    SuppressionReason,
    TemplateVersion,
    TrackingConfig,
)
from .ports import (
    MessageProviderPort,
    MessagingRepositoryPort,
    ProviderReceipt,
    SubmissionRequest,
)

CAP_VERSION = "1.0.0"

CAP_TEMPLATE_PUBLISH = "template.publish"
CAP_CAMPAIGN_CREATE = "campaign.create"
CAP_CAMPAIGN_SCHEDULE = "campaign.schedule"
CAP_CAMPAIGN_SEND = "campaign.send"
CAP_CONSENT_UNSUBSCRIBE = "consent.unsubscribe"

MESSAGING_CAPABILITIES: tuple[str, ...] = (
    CAP_TEMPLATE_PUBLISH,
    CAP_CAMPAIGN_CREATE,
    CAP_CAMPAIGN_SCHEDULE,
    CAP_CAMPAIGN_SEND,
    CAP_CONSENT_UNSUBSCRIBE,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishTemplateVersionCommand:
    template_id: str
    version: int
    subject: str
    html_body: str
    text_body: str
    required_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishTemplateVersionResult:
    template_id: str
    version: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class CreateCampaignCommand:
    name: str
    message_class: str
    template_id: str
    template_version: int
    channel: str = "email"
    purpose: str = "marketing"
    segment_specs: tuple[dict[str, object], ...] = ()
    tracking: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class CreateCampaignResult:
    campaign_id: str
    message_class: str
    template_id: str
    template_version: int
    status: str
    open_tracking: bool
    click_tracking: bool


@dataclass(frozen=True, slots=True)
class ScheduleCampaignCommand:
    campaign_id: str
    schedule: dict[str, object]


@dataclass(frozen=True, slots=True)
class ScheduleCampaignResult:
    campaign_id: str
    status: str
    schedule_kind: str


@dataclass(frozen=True, slots=True)
class SendCampaignCommand:
    campaign_id: str
    recipients: tuple[Recipient, ...] = ()


@dataclass(frozen=True, slots=True)
class SendReceiptView:
    recipient_id: str
    provider_message_id: str
    status: str
    send_at: str
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class SendCampaignResult:
    campaign_id: str
    submitted: int
    deduplicated: int
    segment_excluded: int
    suppressed_excluded: int
    consent_excluded: int
    suppression_leak: int
    receipts: tuple[SendReceiptView, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class UnsubscribeCommand:
    recipient_id: str
    channel: str = "email"
    purpose: str = "marketing"
    reason: str = SuppressionReason.UNSUBSCRIBE.value


@dataclass(frozen=True, slots=True)
class UnsubscribeResult:
    recipient_id: str
    suppressed: bool
    reason: str


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class PublishTemplateVersion:
    """``template.publish`` — write an IMMUTABLE template version (FR-MSG-002)."""

    def __init__(self, *, repository: MessagingRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishTemplateVersionResult:
        command = _typed(request, PublishTemplateVersionCommand)
        organization_id = _tenant(request)
        template = TemplateVersion(
            template_id=command.template_id,
            version=command.version,
            subject=command.subject,
            html_body=command.html_body,
            text_body=command.text_body,
            required_variables=tuple(command.required_variables),
        )
        # The repository rejects an already-published (template_id, version) — immutability.
        self._repo.save_template_version(organization_id=organization_id, template=template)
        return PublishTemplateVersionResult(
            template_id=template.template_id,
            version=template.version,
            content_hash=template.content_hash,
        )


class CreateCampaign:
    """``campaign.create`` — bind an exact template version + a safe segment (FR-MSG-002/003)."""

    def __init__(self, *, repository: MessagingRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateCampaignResult:
        command = _typed(request, CreateCampaignCommand)
        organization_id = _tenant(request)
        message_class = MessageClass(command.message_class)
        # Deny-by-default: the campaign can only bind an EXISTING, immutable template version.
        template = self._repo.get_template_version(
            organization_id=organization_id,
            template_id=command.template_id,
            version=command.template_version,
        )
        if template is None:
            raise TemplateVersionNotFound(command.template_id, command.template_version)
        # Building the Segment validates every criterion against the approved allowlists; a
        # raw-query / arbitrary-DB segment is rejected here before persistence (FR-MSG-003).
        segment = Segment.from_specs(tuple(command.segment_specs))
        tracking = TrackingConfig.from_dict(command.tracking)
        campaign = Campaign(
            organization_id=organization_id,
            campaign_id=self._id_factory(),
            name=command.name,
            message_class=message_class,
            template_id=command.template_id,
            template_version=command.template_version,
            channel=command.channel,
            purpose=command.purpose,
            segment=segment,
            tracking=tracking,
            status=CampaignStatus.DRAFT,
        )
        self._repo.add_campaign(organization_id=organization_id, campaign=campaign)
        return CreateCampaignResult(
            campaign_id=campaign.campaign_id,
            message_class=campaign.message_class.value,
            template_id=campaign.template_id,
            template_version=campaign.template_version,
            status=campaign.status.value,
            open_tracking=campaign.tracking.open_tracking,
            click_tracking=campaign.tracking.click_tracking,
        )


class ScheduleCampaign:
    """``campaign.schedule`` — attach a recipient-time-zone-aware schedule (FR-MSG-004)."""

    def __init__(self, *, repository: MessagingRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ScheduleCampaignResult:
        command = _typed(request, ScheduleCampaignCommand)
        organization_id = _tenant(request)
        campaign = self._load_campaign(organization_id, command.campaign_id)
        schedule = Schedule.from_dict(dict(command.schedule))
        scheduled = replace(campaign, schedule=schedule, status=CampaignStatus.SCHEDULED)
        self._repo.update_campaign(organization_id=organization_id, campaign=scheduled)
        return ScheduleCampaignResult(
            campaign_id=scheduled.campaign_id,
            status=scheduled.status.value,
            schedule_kind=schedule.kind.value,
        )

    def _load_campaign(self, organization_id: str, campaign_id: str) -> Campaign:
        campaign = self._repo.get_campaign(organization_id=organization_id, campaign_id=campaign_id)
        if campaign is None:
            raise CampaignNotFound(campaign_id)
        return campaign


class SendCampaign:
    """``campaign.send`` — ALWAYS honour consent/suppression + submit IDEMPOTENTLY (FR-MSG-005/006).

    For each candidate recipient the send: (1) applies the campaign segment; (2) for a MARKETING
    campaign, excludes any suppressed / unsubscribed / non-consented recipient — this is the
    non-waivable ``suppression_leak == 0`` invariant; a legitimately-required TRANSACTIONAL message
    is not blocked by a marketing opt-out (FR-MSG-001); (3) resolves the send-at per recipient time
    zone (FR-MSG-004); (4) renders the bound immutable template version deterministically; and (5)
    submits to the provider under a stable ``(campaign, recipient, key)`` idempotency key, reusing
    an existing delivery instead of double-sending (FR-MSG-006).
    """

    def __init__(
        self,
        *,
        repository: MessagingRepositoryPort,
        provider: MessageProviderPort,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._provider = provider
        self._clock = clock

    def handle(self, request: object) -> SendCampaignResult:
        command = _typed(request, SendCampaignCommand)
        organization_id = _tenant(request)
        campaign = self._repo.get_campaign(
            organization_id=organization_id, campaign_id=command.campaign_id
        )
        if campaign is None:
            raise CampaignNotFound(command.campaign_id)
        template = self._repo.get_template_version(
            organization_id=organization_id,
            template_id=campaign.template_id,
            version=campaign.template_version,
        )
        if template is None:
            raise TemplateVersionNotFound(campaign.template_id, campaign.template_version)

        now = self._clock()
        segment_excluded = 0
        suppressed_excluded = 0
        consent_excluded = 0
        deduplicated = 0
        suppression_leak = 0
        receipts: list[SendReceiptView] = []

        for recipient in command.recipients:
            if not campaign.segment.matches(recipient.attributes):
                segment_excluded += 1
                continue

            if campaign.message_class.is_suppressible:
                if self._repo.is_suppressed(
                    organization_id=organization_id, recipient_id=recipient.recipient_id
                ):
                    suppressed_excluded += 1
                    continue
                consent = self._repo.get_consent(
                    organization_id=organization_id,
                    recipient_id=recipient.recipient_id,
                    channel=campaign.channel,
                    purpose=campaign.purpose,
                )
                if consent is None or not consent.consented:
                    consent_excluded += 1
                    continue

            send_at = campaign.schedule.resolve_for(recipient_timezone=recipient.timezone, now=now)
            rendered = template.render(recipient.variables)
            key = f"{campaign.campaign_id}:{recipient.recipient_id}"

            existing = self._repo.get_delivery(
                organization_id=organization_id,
                campaign_id=campaign.campaign_id,
                recipient_id=recipient.recipient_id,
                idempotency_key=key,
            )
            if existing is not None:
                deduplicated += 1
                receipts.append(
                    SendReceiptView(
                        recipient_id=recipient.recipient_id,
                        provider_message_id=existing.provider_message_id,
                        status=existing.status.value,
                        send_at=existing.send_at.isoformat(),
                        deduplicated=True,
                    )
                )
                continue

            receipt = self._provider.submit(
                SubmissionRequest(
                    idempotency_key=key,
                    address=recipient.address,
                    subject=rendered.subject,
                    html_body=rendered.html_body,
                    text_body=rendered.text_body,
                    open_tracking=campaign.tracking.open_tracking,
                    click_tracking=campaign.tracking.click_tracking,
                )
            )
            inserted = self._repo.record_delivery(
                organization_id=organization_id,
                receipt=DeliveryReceipt(
                    organization_id=organization_id,
                    campaign_id=campaign.campaign_id,
                    recipient_id=recipient.recipient_id,
                    idempotency_key=key,
                    provider_message_id=receipt.provider_message_id,
                    status=receipt.status,
                    send_at=send_at,
                ),
            )
            if not inserted or receipt.deduplicated:
                deduplicated += 1
            # Defensive re-check: a marketing send must never contain a suppressed/non-consented
            # recipient. This must stay 0 (non-waivable, EVAL-MSG-001).
            if campaign.message_class.is_suppressible and self._repo.is_suppressed(
                organization_id=organization_id, recipient_id=recipient.recipient_id
            ):
                suppression_leak += 1
            receipts.append(
                SendReceiptView(
                    recipient_id=recipient.recipient_id,
                    provider_message_id=receipt.provider_message_id,
                    status=receipt.status.value,
                    send_at=send_at.isoformat(),
                    deduplicated=receipt.deduplicated,
                )
            )

        if campaign.status is not CampaignStatus.SENT:
            self._repo.update_campaign(
                organization_id=organization_id,
                campaign=replace(campaign, status=CampaignStatus.SENT),
            )

        submitted = len([r for r in receipts if not r.deduplicated])
        return SendCampaignResult(
            campaign_id=campaign.campaign_id,
            submitted=submitted,
            deduplicated=deduplicated,
            segment_excluded=segment_excluded,
            suppressed_excluded=suppressed_excluded,
            consent_excluded=consent_excluded,
            suppression_leak=suppression_leak,
            receipts=tuple(receipts),
        )


class Unsubscribe:
    """``consent.unsubscribe`` — suppress a recipient immediately + idempotently (FR-MSG-005)."""

    def __init__(self, *, repository: MessagingRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> UnsubscribeResult:
        command = _typed(request, UnsubscribeCommand)
        organization_id = _tenant(request)
        reason = SuppressionReason(command.reason)
        # Suppression is the authoritative marketing block; record consent=False too so both the
        # suppression check and the consent check exclude the recipient (defense-in-depth).
        self._repo.add_suppression(
            organization_id=organization_id,
            entry=SuppressionEntry(
                organization_id=organization_id,
                recipient_id=command.recipient_id,
                reason=reason,
            ),
        )
        self._repo.set_consent(
            organization_id=organization_id,
            consent=ConsentRecord(
                organization_id=organization_id,
                recipient_id=command.recipient_id,
                channel=command.channel,
                purpose=command.purpose,
                consented=False,
            ),
        )
        return UnsubscribeResult(
            recipient_id=command.recipient_id,
            suppressed=True,
            reason=reason.value,
        )


__all__ = [
    "CAP_CAMPAIGN_CREATE",
    "CAP_CAMPAIGN_SCHEDULE",
    "CAP_CAMPAIGN_SEND",
    "CAP_CONSENT_UNSUBSCRIBE",
    "CAP_TEMPLATE_PUBLISH",
    "CAP_VERSION",
    "MESSAGING_CAPABILITIES",
    "RES_CAMPAIGN",
    "CreateCampaign",
    "CreateCampaignCommand",
    "CreateCampaignResult",
    "DeliveryStatus",
    "ProviderReceipt",
    "PublishTemplateVersion",
    "PublishTemplateVersionCommand",
    "PublishTemplateVersionResult",
    "Recipient",
    "ScheduleCampaign",
    "ScheduleCampaignCommand",
    "ScheduleCampaignResult",
    "SendCampaign",
    "SendCampaignCommand",
    "SendCampaignResult",
    "SendReceiptView",
    "Unsubscribe",
    "UnsubscribeCommand",
    "UnsubscribeResult",
]
