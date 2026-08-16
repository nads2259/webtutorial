"""Commerce capabilities: one authoritative implementation per action (LAW-04, docs/29).

Every handler runs through the kernel command/query bus, so each invocation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The commerce invariants are enforced here by construction and are never weakened:

* ``commerce.offer.publish`` validates the offer against ``commerce-offer.schema.json`` invariants
  and composes free/paid/tier access (FR-COM-001/002).
* ``commerce.purchase`` grants entitlements THROUGH the existing entitlement engine (via
  :class:`EntitlementGrantPort`), never by branching on plan/payment-provider names (ARCH-019). A
  free offer fulfils immediately; a paid offer awaits a signed payment callback.
* ``commerce.payment.callback`` VERIFIES the provider signature before acting; a forged / unsigned /
  tampered / replayed callback is REJECTED (fail closed) and never mutates entitlements, while a
  correctly-signed callback is processed IDEMPOTENTLY — the same ``event_id`` grants exactly once
  (FR-COM-003).
* ``commerce.refund.issue`` revokes the granted entitlement IDEMPOTENTLY and records an auditable
  refund (FR-COM-004).
* ``commerce.ad.disclose`` records an advertising/sponsorship surface ALWAYS flagged disclosed
  (FR-COM-005).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.errors import CallbackRejected, PurchaseNotFound, TenantScopeMissing
from ..domain.model import (
    RES_COMMERCE,
    AdKind,
    Offer,
    OfferStatus,
    PaymentCallbackEnvelope,
    PaymentEventType,
    PurchaseStatus,
    SponsoredPlacement,
)
from ..domain.model import (
    Purchase as PurchaseModel,
)
from .ports import CommerceRepositoryPort, EntitlementGrantPort, WebhookVerifierPort

CAP_VERSION = "1.0.0"

CAP_OFFER_PUBLISH = "commerce.offer.publish"
CAP_PURCHASE = "commerce.purchase"
CAP_PAYMENT_CALLBACK = "commerce.payment.callback"
CAP_REFUND_ISSUE = "commerce.refund.issue"
CAP_AD_DISCLOSE = "commerce.ad.disclose"

COMMERCE_CAPABILITIES: tuple[str, ...] = (
    CAP_OFFER_PUBLISH,
    CAP_PURCHASE,
    CAP_PAYMENT_CALLBACK,
    CAP_REFUND_ISSUE,
    CAP_AD_DISCLOSE,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishOfferCommand:
    offer: dict[str, object]
    product_name: str | None = None
    product_kind: str = "course"


@dataclass(frozen=True, slots=True)
class PublishOfferResult:
    offer_id: str
    version: str
    status: str
    is_free: bool
    contract: dict[str, object]


@dataclass(frozen=True, slots=True)
class PurchaseCommand:
    offer_id: str
    offer_version: str


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    purchase_id: str
    status: str
    fulfilled: bool
    grant_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaymentCallbackCommand:
    event_id: str
    event_type: str
    provider: str
    purchase_id: str
    amount_minor: int
    currency: str
    signature: str
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PaymentCallbackResult:
    accepted: bool
    purchase_id: str
    status: str
    grant_ids: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class IssueRefundCommand:
    purchase_id: str


@dataclass(frozen=True, slots=True)
class IssueRefundResult:
    refund_id: str | None
    purchase_id: str
    status: str
    revoked_grant_ids: tuple[str, ...]
    already_refunded: bool


@dataclass(frozen=True, slots=True)
class DiscloseAdCommand:
    placement_id: str
    kind: str
    disclosure_label: str


@dataclass(frozen=True, slots=True)
class DiscloseAdResult:
    placement_id: str
    kind: str
    disclosed: bool
    is_advertising: bool
    disclosure_label: str = field(default="")


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


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return str(subject)


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class PublishOffer:
    """``commerce.offer.publish`` — validate + publish a schema-valid, composed offer."""

    def __init__(self, *, repository: CommerceRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishOfferResult:
        command = _typed(request, PublishOfferCommand)
        organization_id = _tenant(request)
        # from_dict enforces the offer invariants: a malformed price/grant is rejected here.
        offer = Offer.from_dict(dict(command.offer))
        if offer.status is OfferStatus.DRAFT:
            offer = _with_status(offer, OfferStatus.ACTIVE)
        from ..domain.model import Product

        product = Product(
            product_id=offer.product_id,
            name=command.product_name or offer.product_id,
            kind=command.product_kind,
        )
        self._repo.add_product(organization_id=organization_id, product=product)
        self._repo.upsert_offer(organization_id=organization_id, offer=offer)
        return PublishOfferResult(
            offer_id=offer.offer_id,
            version=offer.version,
            status=offer.status.value,
            is_free=offer.is_free,
            contract=offer.to_contract(),
        )


class Purchase:
    """``commerce.purchase`` — create a purchase; grant entitlements via the existing engine."""

    def __init__(
        self,
        *,
        repository: CommerceRepositoryPort,
        entitlements: EntitlementGrantPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._entitlements = entitlements
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> PurchaseResult:
        command = _typed(request, PurchaseCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        offer = self._repo.get_offer(
            organization_id=organization_id,
            offer_id=command.offer_id,
            version=command.offer_version,
        )
        if offer is None:
            from ..domain.errors import OfferNotFound

            raise OfferNotFound(command.offer_id)
        now = self._clock()
        purchase = PurchaseModel(
            purchase_id=self._id_factory(),
            offer_id=offer.offer_id,
            offer_version=offer.version,
            product_id=offer.product_id,
            subject_id=subject_id,
            status=PurchaseStatus.PENDING_PAYMENT,
            created_at=now,
            organization_id=organization_id,
        )
        self._repo.add_purchase(organization_id=organization_id, purchase=purchase)
        if offer.is_free:
            # A free offer needs no payment provider; it fulfils immediately via the entitlement
            # engine (grant origin = free policy, mapped by the adapter — no plan-name branching).
            grant_ids = _grant_entitlements(
                entitlements=self._entitlements,
                offer=offer,
                subject_id=subject_id,
                organization_id=organization_id,
                now=now,
            )
            purchase = purchase.fulfilled(grant_ids=grant_ids, now=now)
            self._repo.save_purchase(organization_id=organization_id, purchase=purchase)
        return PurchaseResult(
            purchase_id=purchase.purchase_id,
            status=purchase.status.value,
            fulfilled=purchase.is_fulfilled,
            grant_ids=purchase.grant_ids,
        )


class ProcessPaymentCallback:
    """``commerce.payment.callback`` — verify signature, then process idempotently (FR-COM-003)."""

    def __init__(
        self,
        *,
        repository: CommerceRepositoryPort,
        verifier: WebhookVerifierPort,
        entitlements: EntitlementGrantPort,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._verifier = verifier
        self._entitlements = entitlements
        self._clock = clock

    def handle(self, request: object) -> PaymentCallbackResult:
        command = _typed(request, PaymentCallbackCommand)
        organization_id = _tenant(request)
        try:
            event_type = PaymentEventType(command.event_type)
        except ValueError as exc:
            raise CallbackRejected("unknown_event_type") from exc
        envelope = PaymentCallbackEnvelope(
            event_id=command.event_id,
            event_type=event_type,
            provider=command.provider,
            purchase_id=command.purchase_id,
            amount_minor=command.amount_minor,
            currency=command.currency,
            signature=command.signature,
            occurred_at=command.occurred_at,
        )
        # FAIL CLOSED: verify the signature BEFORE any state/entitlement mutation. A forged /
        # unsigned / tampered callback is rejected here and never grants or revokes anything.
        if not self._verifier.verify(envelope):
            raise CallbackRejected("signature_invalid")

        # IDEMPOTENCY: the same event_id has a single effect even if replayed.
        newly = self._repo.record_payment_event(
            organization_id=organization_id,
            event_id=envelope.event_id,
            event_type=envelope.event_type.value,
            purchase_id=envelope.purchase_id,
        )
        purchase = self._repo.get_purchase(
            organization_id=organization_id, purchase_id=envelope.purchase_id
        )
        if purchase is None:
            raise PurchaseNotFound(envelope.purchase_id)
        if not newly:
            # Replay of an already-processed event: report the current state, no new effect.
            return PaymentCallbackResult(
                accepted=True,
                purchase_id=purchase.purchase_id,
                status=purchase.status.value,
                grant_ids=purchase.grant_ids,
                replayed=True,
            )

        now = self._clock()
        if envelope.event_type is PaymentEventType.PAYMENT_SUCCEEDED:
            purchase = self._fulfil(purchase, organization_id=organization_id, now=now)
        else:  # PAYMENT_REFUNDED
            purchase = self._refund(purchase, organization_id=organization_id, now=now)
        return PaymentCallbackResult(
            accepted=True,
            purchase_id=purchase.purchase_id,
            status=purchase.status.value,
            grant_ids=purchase.grant_ids,
            replayed=False,
        )

    def _fulfil(
        self, purchase: PurchaseModel, *, organization_id: str, now: datetime
    ) -> PurchaseModel:
        if purchase.is_fulfilled:
            return purchase
        offer = self._repo.get_offer(
            organization_id=organization_id,
            offer_id=purchase.offer_id,
            version=purchase.offer_version,
        )
        if offer is None:
            from ..domain.errors import OfferNotFound

            raise OfferNotFound(purchase.offer_id)
        grant_ids = _grant_entitlements(
            entitlements=self._entitlements,
            offer=offer,
            subject_id=purchase.subject_id,
            organization_id=organization_id,
            now=now,
        )
        fulfilled = purchase.fulfilled(grant_ids=grant_ids, now=now)
        self._repo.save_purchase(organization_id=organization_id, purchase=fulfilled)
        return fulfilled

    def _refund(
        self, purchase: PurchaseModel, *, organization_id: str, now: datetime
    ) -> PurchaseModel:
        for grant_id in purchase.grant_ids:
            self._entitlements.revoke(grant_id=grant_id, now=now, organization_id=organization_id)
        refunded = purchase.refunded(now=now)
        self._repo.save_purchase(organization_id=organization_id, purchase=refunded)
        return refunded


class IssueRefund:
    """``commerce.refund.issue`` — revoke the granted entitlement idempotently + auditable."""

    def __init__(
        self,
        *,
        repository: CommerceRepositoryPort,
        entitlements: EntitlementGrantPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._entitlements = entitlements
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> IssueRefundResult:
        command = _typed(request, IssueRefundCommand)
        organization_id = _tenant(request)
        purchase = self._repo.get_purchase(
            organization_id=organization_id, purchase_id=command.purchase_id
        )
        if purchase is None:
            raise PurchaseNotFound(command.purchase_id)
        if purchase.is_refunded:
            # Idempotent: refunding an already-refunded purchase is a no-op (no double revoke).
            return IssueRefundResult(
                refund_id=None,
                purchase_id=purchase.purchase_id,
                status=purchase.status.value,
                revoked_grant_ids=(),
                already_refunded=True,
            )
        now = self._clock()
        revoked: list[str] = []
        for grant_id in purchase.grant_ids:
            if self._entitlements.revoke(
                grant_id=grant_id, now=now, organization_id=organization_id
            ):
                revoked.append(grant_id)
        refunded = purchase.refunded(now=now)
        self._repo.save_purchase(organization_id=organization_id, purchase=refunded)
        refund_id = self._id_factory()
        self._repo.add_refund(
            organization_id=organization_id,
            refund_id=refund_id,
            purchase_id=purchase.purchase_id,
            now=now,
        )
        return IssueRefundResult(
            refund_id=refund_id,
            purchase_id=purchase.purchase_id,
            status=refunded.status.value,
            revoked_grant_ids=tuple(revoked),
            already_refunded=False,
        )


class DiscloseAd:
    """``commerce.ad.disclose`` — record an advertising/sponsorship surface, always disclosed."""

    def __init__(self, *, repository: CommerceRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> DiscloseAdResult:
        command = _typed(request, DiscloseAdCommand)
        organization_id = _tenant(request)
        # Construction forces ``disclosed=True`` + a non-empty label: an undisclosed sponsored
        # surface cannot be created (FR-COM-005).
        placement = SponsoredPlacement(
            placement_id=command.placement_id,
            kind=AdKind(command.kind),
            disclosure_label=command.disclosure_label,
        )
        self._repo.add_placement(organization_id=organization_id, placement=placement)
        return DiscloseAdResult(
            placement_id=placement.placement_id,
            kind=placement.kind.value,
            disclosed=placement.disclosed,
            is_advertising=placement.is_advertising,
            disclosure_label=placement.disclosure_label,
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _grant_entitlements(
    *,
    entitlements: EntitlementGrantPort,
    offer: Offer,
    subject_id: str,
    organization_id: str,
    now: datetime,
) -> tuple[str, ...]:
    """Grant each of the offer's capability scopes through the existing entitlement engine.

    The offer's structural ``billing_type`` (free/one_time/recurring/invoice) is passed to the
    entitlement seam, which maps it to an entitlement grant ORIGIN TYPE — the domain never learns a
    plan or payment-provider name (ARCH-019).
    """
    grant_ids: list[str] = []
    for grant in offer.grants:
        grant_id = entitlements.grant(
            subject_id=subject_id,
            capability=grant.capability,
            billing_type=offer.price.billing_type,
            starts_at=now,
            organization_id=organization_id,
        )
        grant_ids.append(grant_id)
    return tuple(grant_ids)


def _with_status(offer: Offer, status: OfferStatus) -> Offer:
    return Offer(
        offer_id=offer.offer_id,
        version=offer.version,
        product_id=offer.product_id,
        status=status,
        price=offer.price,
        grants=offer.grants,
        terms_version=offer.terms_version,
        eligibility=offer.eligibility,
        effective_from=offer.effective_from,
        effective_until=offer.effective_until,
    )


__all__ = [
    "CAP_AD_DISCLOSE",
    "CAP_OFFER_PUBLISH",
    "CAP_PAYMENT_CALLBACK",
    "CAP_PURCHASE",
    "CAP_REFUND_ISSUE",
    "CAP_VERSION",
    "COMMERCE_CAPABILITIES",
    "RES_COMMERCE",
    "DiscloseAd",
    "DiscloseAdCommand",
    "DiscloseAdResult",
    "IssueRefund",
    "IssueRefundCommand",
    "IssueRefundResult",
    "PaymentCallbackCommand",
    "PaymentCallbackResult",
    "ProcessPaymentCallback",
    "PublishOffer",
    "PublishOfferCommand",
    "PublishOfferResult",
    "Purchase",
    "PurchaseCommand",
    "PurchaseResult",
]
