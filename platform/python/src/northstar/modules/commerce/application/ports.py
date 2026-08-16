"""Ports (abstractions) for the commerce application layer (rule 10/20, DIP).

Three seams keep the capabilities infrastructure-free and hold no ambient authority (rule 50):

* :class:`CommerceRepositoryPort` — the module's OWN tenant-scoped persistence for products, offers,
  purchases, refunds, processed payment events and ad placements (LAW-13).
* :class:`WebhookVerifierPort` — verifies a payment provider callback signature (FR-COM-003). The
  reference adapter is an HMAC verifier; a real provider (Stripe/etc.) is a drop-in swap behind this
  same port. Verification fails CLOSED.
* :class:`EntitlementGrantPort` — the seam commerce depends on to grant/revoke entitlements and ask
  entitlement *decisions*. The reference adapter REUSES the existing entitlement engine
  (``northstar.modules.entitlement``); commerce never re-implements entitlement logic and never
  branches on plan/payment-provider names (ARCH-019).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.model import (
    BillingType,
    Offer,
    PaymentCallbackEnvelope,
    Product,
    Purchase,
    SponsoredPlacement,
)


@runtime_checkable
class WebhookVerifierPort(Protocol):
    """Verifies a payment provider callback signature (FR-COM-003), failing CLOSED.

    Returns ``True`` only when the signature recomputes over the callback's signing material with a
    trusted provider key (constant-time comparison). A forged / unsigned / tampered callback returns
    ``False`` (or raises), so the capability never mutates entitlements for it.
    """

    def verify(self, envelope: PaymentCallbackEnvelope) -> bool: ...


@runtime_checkable
class EntitlementGrantPort(Protocol):
    """Commerce's seam onto the existing entitlement engine (ARCH-019).

    Commerce asks for entitlement *grants*, *revocations* and *decisions* — never for a plan or
    payment-provider name. ``billing_type`` is a structural offer property (free/one_time/recurring/
    invoice); the adapter maps it to an entitlement grant ORIGIN TYPE, never a marketing plan name.
    """

    def grant(
        self,
        *,
        subject_id: str,
        capability: str,
        billing_type: BillingType,
        starts_at: datetime,
        ends_at: datetime | None = None,
        organization_id: str,
    ) -> str: ...

    def revoke(self, *, grant_id: str, now: datetime, organization_id: str) -> bool: ...

    def is_entitled(self, *, subject_id: str, action: str, organization_id: str) -> bool: ...


@runtime_checkable
class CommerceRepositoryPort(Protocol):
    """Persists/reads the commerce module's OWN tenant-scoped data (rule 50, LAW-13)."""

    # Catalog ------------------------------------------------------------
    def add_product(self, *, organization_id: str, product: Product) -> None: ...

    def upsert_offer(self, *, organization_id: str, offer: Offer) -> None: ...

    def get_offer(self, *, organization_id: str, offer_id: str, version: str) -> Offer | None: ...

    # Purchases ----------------------------------------------------------
    def add_purchase(self, *, organization_id: str, purchase: Purchase) -> None: ...

    def get_purchase(self, *, organization_id: str, purchase_id: str) -> Purchase | None: ...

    def save_purchase(self, *, organization_id: str, purchase: Purchase) -> None: ...

    # Payment-event idempotency ledger -----------------------------------
    def record_payment_event(
        self, *, organization_id: str, event_id: str, event_type: str, purchase_id: str
    ) -> bool:
        """Record a processed provider event; return ``True`` if newly recorded, ``False`` if the
        event was already processed (a replay). Enables single-effect idempotency (FR-COM-003)."""
        ...

    # Refunds ------------------------------------------------------------
    def add_refund(
        self, *, organization_id: str, refund_id: str, purchase_id: str, now: datetime
    ) -> None: ...

    # Ad placements ------------------------------------------------------
    def add_placement(self, *, organization_id: str, placement: SponsoredPlacement) -> None: ...

    def list_placements(self, *, organization_id: str) -> Sequence[SponsoredPlacement]: ...
