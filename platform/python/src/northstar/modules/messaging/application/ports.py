"""Ports (abstractions) for the messaging application layer (rule 10/20, DIP).

Every infrastructure seam is a Protocol so the capabilities stay infrastructure-free and hold no
ambient authority (rule 50):

* :class:`MessageProviderPort` — a provider-neutral send seam (docs/16 §8). The reference in-memory
  adapter implements it; a real ESP/SMS/push provider is a drop-in adapter swap. Submission is
  IDEMPOTENT by ``idempotency_key``: re-submitting the same key never produces a second visible
  send, and a transient failure can be retried safely (FR-MSG-006).
* :class:`MessagingRepositoryPort` — the module's own tenant-scoped persistence for template
  versions, campaigns, consent, suppression and delivery receipts (LAW-13). Recording a delivery is
  idempotent on ``(organization_id, campaign_id, recipient_id, idempotency_key)`` and publishing a
  template version rejects an already-published ``(template_id, version)`` (FR-MSG-002/006).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.model import (
    Campaign,
    ConsentRecord,
    DeliveryReceipt,
    DeliveryStatus,
    SuppressionEntry,
    TemplateVersion,
)


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    """A single provider submission carrying its idempotency key (docs/16 §8, FR-MSG-006)."""

    idempotency_key: str
    address: str
    subject: str
    html_body: str
    text_body: str
    open_tracking: bool = False
    click_tracking: bool = False


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    """The provider's neutral acknowledgement of a submission (docs/16 §8)."""

    provider_message_id: str
    status: DeliveryStatus
    deduplicated: bool = False


@runtime_checkable
class MessageProviderPort(Protocol):
    """Submits a rendered message to a provider IDEMPOTENTLY by key (FR-MSG-006, docs/16 §8).

    Implementations MUST guarantee that two submissions with the same ``idempotency_key`` result in
    at most one visible send (returning the prior ``provider_message_id`` with ``deduplicated=True``
    on the second call). A transient provider error is raised (never swallowed) so the caller can
    retry; a retry with the same key must still yield a single send.
    """

    def submit(self, request: SubmissionRequest) -> ProviderReceipt: ...


@runtime_checkable
class MessagingRepositoryPort(Protocol):
    """Persists/reads messaging state, always tenant-scoped (rule 50, LAW-13)."""

    # Templates (immutable versions) -------------------------------------
    def save_template_version(self, *, organization_id: str, template: TemplateVersion) -> None:
        """Persist a NEW template version; reject an already-published ``(template_id, version)``
        with ``TemplateVersionAlreadyPublished`` (immutability, FR-MSG-002)."""
        ...

    def get_template_version(
        self, *, organization_id: str, template_id: str, version: int
    ) -> TemplateVersion | None: ...

    def get_latest_template(
        self, *, organization_id: str, template_id: str
    ) -> TemplateVersion | None:
        """Return the highest published version of ``template_id`` (or ``None``)."""
        ...

    def list_templates(self, *, organization_id: str) -> Sequence[TemplateVersion]:
        """Return the latest version of every distinct template id in the tenant."""
        ...

    # Campaigns ----------------------------------------------------------
    def add_campaign(self, *, organization_id: str, campaign: Campaign) -> None: ...

    def get_campaign(self, *, organization_id: str, campaign_id: str) -> Campaign | None: ...

    def update_campaign(self, *, organization_id: str, campaign: Campaign) -> None: ...

    # Consent + suppression ---------------------------------------------
    def set_consent(self, *, organization_id: str, consent: ConsentRecord) -> None: ...

    def get_consent(
        self, *, organization_id: str, recipient_id: str, channel: str, purpose: str
    ) -> ConsentRecord | None: ...

    def add_suppression(self, *, organization_id: str, entry: SuppressionEntry) -> None:
        """Suppress a recipient; idempotent — re-suppressing keeps a single entry (FR-MSG-005)."""
        ...

    def is_suppressed(self, *, organization_id: str, recipient_id: str) -> bool: ...

    # Delivery receipts (idempotent submission evidence) ----------------
    def get_delivery(
        self,
        *,
        organization_id: str,
        campaign_id: str,
        recipient_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt | None: ...

    def record_delivery(self, *, organization_id: str, receipt: DeliveryReceipt) -> bool:
        """Persist a delivery receipt; return ``True`` if newly inserted, ``False`` if the same
        ``(campaign, recipient, idempotency_key)`` already existed (idempotent, FR-MSG-006)."""
        ...

    def list_deliveries(
        self, *, organization_id: str, campaign_id: str
    ) -> Sequence[DeliveryReceipt]: ...


__all__ = [
    "DeliveryStatus",
    "MessageProviderPort",
    "MessagingRepositoryPort",
    "ProviderReceipt",
    "SubmissionRequest",
]
