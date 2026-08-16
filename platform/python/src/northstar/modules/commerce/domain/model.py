"""Commerce domain model: offers, prices, grants, purchases, refunds and ad disclosure.

Mirrors docs/29 §2/§3 (offer composition) and the ``commerce-offer.schema.json`` contract. Every
type here is pure and infrastructure-free (rule 10, LAW-02): the domain enforces the same invariants
as the JSON Schema by construction, and a contract test independently validates a produced offer
dict against the schema.

Key invariants enforced by construction (never weakened):

* an :class:`Offer` composes free/paid/tier access — it MUST declare a price, a terms version and at
  least one :class:`Grant`; a malformed price/grant is rejected (FR-COM-001/002);
* offers are versioned and a :class:`Purchase` references the exact accepted offer version
  (docs/29 §3);
* a :class:`SponsoredPlacement` is ALWAYS flagged disclosed with a non-empty label (FR-COM-005) —
  an undisclosed sponsored/advertising surface cannot be constructed;
* the offer/purchase model carries NO plan or payment-provider names — only structural billing
  *types* and capability grant scopes (ARCH-019).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .errors import AdDisclosureRequired, OfferValidationError

# Stable resource vocabulary (contract): commerce surfaces are tenant-scoped resources.
RES_COMMERCE = "commerce.catalog"

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class OfferStatus(StrEnum):
    """Offer lifecycle status (schema ``status``)."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class BillingType(StrEnum):
    """How an offer is billed (schema ``price.billing_type``) — a *type*, never a plan name."""

    FREE = "free"
    ONE_TIME = "one_time"
    RECURRING = "recurring"
    INVOICE = "invoice"


class PurchaseStatus(StrEnum):
    """Purchase lifecycle status (docs/29 §2)."""

    PENDING_PAYMENT = "pending_payment"
    FULFILLED = "fulfilled"
    REFUNDED = "refunded"


class AdKind(StrEnum):
    """Advertising/sponsorship surface kind (docs/29 §5)."""

    CONTEXTUAL = "contextual"
    BEHAVIORAL = "behavioral"
    SPONSORSHIP = "sponsorship"
    HOUSE = "house"


class PaymentEventType(StrEnum):
    """The provider-neutral payment lifecycle events a callback may carry (docs/29 §9)."""

    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_REFUNDED = "payment_refunded"


@dataclass(frozen=True, slots=True)
class PaymentCallbackEnvelope:
    """A provider webhook payload (docs/29 §9). The provider is an opaque *reference*, never a name
    the domain branches on (ARCH-019).

    :func:`signing_payload` binds every business field so tampering with any of them breaks the
    signature; the ``signature`` itself is verified in the adapter behind ``WebhookVerifierPort``.
    """

    event_id: str
    event_type: PaymentEventType
    provider: str
    purchase_id: str
    amount_minor: int
    currency: str
    signature: str
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise OfferValidationError("payment callback must carry an event_id")
        if not self.purchase_id:
            raise OfferValidationError("payment callback must reference a purchase_id")


def signing_payload(envelope: PaymentCallbackEnvelope) -> bytes:
    """Deterministic canonical signing material for a payment callback (excludes the signature).

    Every business field is bound so a tampered amount / purchase / event id / type invalidates the
    signature (FR-COM-003). Pure and stable across processes.
    """
    occurred = envelope.occurred_at.isoformat() if envelope.occurred_at else ""
    parts = (
        envelope.event_id,
        envelope.event_type.value,
        envelope.provider,
        envelope.purchase_id,
        str(envelope.amount_minor),
        envelope.currency,
        occurred,
    )
    return "\n".join(parts).encode("utf-8")


# ---------------------------------------------------------------------------
# Offer composition (FR-COM-001/002)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Price:
    """An offer price: integer minor units + ISO-4217 currency + a billing *type* (docs/29 §2)."""

    amount_minor: int
    currency: str
    billing_type: BillingType
    interval: str | None = None

    def __post_init__(self) -> None:
        if self.amount_minor < 0:
            raise OfferValidationError("price.amount_minor must be >= 0")
        if not _CURRENCY.match(self.currency):
            raise OfferValidationError("price.currency must be a 3-letter ISO-4217 code")
        if not isinstance(self.billing_type, BillingType):
            raise OfferValidationError("price.billing_type must be a valid BillingType")
        if self.billing_type is BillingType.FREE and self.amount_minor != 0:
            raise OfferValidationError("a free offer must have amount_minor == 0")

    @property
    def is_free(self) -> bool:
        return self.billing_type is BillingType.FREE

    def to_dict(self) -> dict[str, object]:
        return {
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "billing_type": self.billing_type.value,
            "interval": self.interval,
        }


@dataclass(frozen=True, slots=True)
class Grant:
    """A capability scope an offer grants (schema ``grants[]``): capability + scope (+ limits)."""

    capability: str
    scope: dict[str, object] = field(default_factory=dict)
    limits: dict[str, object] = field(default_factory=dict)
    duration: str | None = None

    def __post_init__(self) -> None:
        if not self.capability:
            raise OfferValidationError("each grant must declare a non-empty capability")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"capability": self.capability, "scope": dict(self.scope)}
        if self.limits:
            payload["limits"] = dict(self.limits)
        if self.duration is not None:
            payload["duration"] = self.duration
        return payload


@dataclass(frozen=True, slots=True)
class Offer:
    """A versioned, schema-valid offer that composes free/paid/tier access (docs/29 §3).

    Construction enforces the same invariants as ``commerce-offer.schema.json``: a valid semantic
    ``version``, at least one :class:`Grant`, a valid :class:`Price` and a non-empty
    ``terms_version``. Changing an offer creates a NEW version; a purchase references the exact
    accepted version so contractual grants can never be silently reduced (docs/29 §3).
    """

    offer_id: str
    version: str
    product_id: str
    status: OfferStatus
    price: Price
    grants: tuple[Grant, ...]
    terms_version: str
    eligibility: dict[str, object] = field(default_factory=dict)
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    def __post_init__(self) -> None:
        if not self.offer_id:
            raise OfferValidationError("offer_id must be non-empty")
        if not _SEMVER.match(self.version):
            raise OfferValidationError("offer version must be a semantic version")
        if not self.product_id:
            raise OfferValidationError("offer must reference a product_id")
        if not isinstance(self.status, OfferStatus):
            raise OfferValidationError("offer status must be a valid OfferStatus")
        if not isinstance(self.price, Price):
            raise OfferValidationError("offer must declare a valid price")
        if len(self.grants) < 1:
            raise OfferValidationError("offer must declare at least one grant (minItems 1)")
        if not self.terms_version.strip():
            raise OfferValidationError("offer must declare a terms_version")

    @property
    def is_free(self) -> bool:
        return self.price.is_free

    def to_contract(self) -> dict[str, object]:
        """Serialise to the ``commerce-offer`` JSON contract (schema-valid)."""
        return {
            "offer_id": self.offer_id,
            "version": self.version,
            "product_id": self.product_id,
            "status": self.status.value,
            "price": self.price.to_dict(),
            "grants": [g.to_dict() for g in self.grants],
            "eligibility": dict(self.eligibility),
            "terms_version": self.terms_version,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_until": self.effective_until.isoformat() if self.effective_until else None,
        }

    @staticmethod
    def from_dict(raw: dict[str, object]) -> Offer:
        """Build an offer from serialized fields, enforcing the offer invariants."""
        price_raw = raw.get("price")
        if not isinstance(price_raw, dict):
            raise OfferValidationError("offer must declare a price object")
        try:
            billing = BillingType(str(price_raw.get("billing_type", "")))
        except ValueError as exc:
            raise OfferValidationError(
                f"invalid billing_type {price_raw.get('billing_type')!r}"
            ) from exc
        interval = price_raw.get("interval")
        price = Price(
            amount_minor=int(price_raw.get("amount_minor", 0)),
            currency=str(price_raw.get("currency", "")),
            billing_type=billing,
            interval=(str(interval) if interval is not None else None),
        )
        grants_raw = raw.get("grants") or []
        if not isinstance(grants_raw, (list, tuple)):
            raise OfferValidationError("grants must be an array")
        grants = tuple(_grant_from_dict(g) for g in grants_raw)
        try:
            status = OfferStatus(str(raw.get("status", "")))
        except ValueError as exc:
            raise OfferValidationError(f"invalid offer status {raw.get('status')!r}") from exc
        eligibility = raw.get("eligibility") or {}
        if not isinstance(eligibility, dict):
            raise OfferValidationError("eligibility must be an object")
        return Offer(
            offer_id=str(raw.get("offer_id", "")),
            version=str(raw.get("version", "")),
            product_id=str(raw.get("product_id", "")),
            status=status,
            price=price,
            grants=grants,
            terms_version=str(raw.get("terms_version", "")),
            eligibility=dict(eligibility),
            effective_from=_parse_dt(raw.get("effective_from")),
            effective_until=_parse_dt(raw.get("effective_until")),
        )


def _grant_from_dict(raw: object) -> Grant:
    if not isinstance(raw, dict):
        raise OfferValidationError("each grant must be an object")
    scope = raw.get("scope") or {}
    if not isinstance(scope, dict):
        raise OfferValidationError("grant scope must be an object")
    limits = raw.get("limits") or {}
    if not isinstance(limits, dict):
        raise OfferValidationError("grant limits must be an object")
    duration = raw.get("duration")
    return Grant(
        capability=str(raw.get("capability", "")),
        scope=dict(scope),
        limits=dict(limits),
        duration=(str(duration) if duration is not None else None),
    )


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class Product:
    """A sellable product (docs/29 §2). Offers reference a product; a product has >=1 offer."""

    product_id: str
    name: str
    kind: str = "course"

    def __post_init__(self) -> None:
        if not self.product_id:
            raise OfferValidationError("product_id must be non-empty")
        if not self.name.strip():
            raise OfferValidationError("product must declare a non-empty name")


# ---------------------------------------------------------------------------
# Purchases + refunds (FR-COM-003/004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Purchase:
    """A purchase referencing an exact accepted offer version (docs/29 §3)."""

    purchase_id: str
    offer_id: str
    offer_version: str
    product_id: str
    subject_id: str
    status: PurchaseStatus
    created_at: datetime
    grant_ids: tuple[str, ...] = ()
    organization_id: str | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.purchase_id:
            raise OfferValidationError("purchase_id must be non-empty")
        if not self.subject_id:
            raise OfferValidationError("purchase must reference a subject_id")

    @property
    def is_fulfilled(self) -> bool:
        return self.status is PurchaseStatus.FULFILLED

    @property
    def is_refunded(self) -> bool:
        return self.status is PurchaseStatus.REFUNDED

    def fulfilled(self, *, grant_ids: tuple[str, ...], now: datetime) -> Purchase:
        return Purchase(
            purchase_id=self.purchase_id,
            offer_id=self.offer_id,
            offer_version=self.offer_version,
            product_id=self.product_id,
            subject_id=self.subject_id,
            status=PurchaseStatus.FULFILLED,
            created_at=self.created_at,
            grant_ids=grant_ids,
            organization_id=self.organization_id,
            updated_at=now,
        )

    def refunded(self, *, now: datetime) -> Purchase:
        return Purchase(
            purchase_id=self.purchase_id,
            offer_id=self.offer_id,
            offer_version=self.offer_version,
            product_id=self.product_id,
            subject_id=self.subject_id,
            status=PurchaseStatus.REFUNDED,
            created_at=self.created_at,
            grant_ids=self.grant_ids,
            organization_id=self.organization_id,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Advertising / sponsorship disclosure (FR-COM-005)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SponsoredPlacement:
    """An advertising/sponsorship surface that is ALWAYS flagged disclosed (docs/29 §5).

    ``disclosed`` is forced to ``True`` at construction and every non-house surface MUST carry a
    non-empty disclosure label (FR-COM-005): an undisclosed sponsored/advertising surface cannot be
    constructed, so a sponsored surface can never be presented as organic content.
    """

    placement_id: str
    kind: AdKind
    disclosure_label: str
    disclosed: bool = True

    def __post_init__(self) -> None:
        if not self.placement_id:
            raise AdDisclosureRequired()
        if self.disclosed is not True:
            raise AdDisclosureRequired()
        if not self.disclosure_label.strip():
            raise AdDisclosureRequired()

    @property
    def is_advertising(self) -> bool:
        """Contextual/behavioral/sponsorship surfaces are ads; house promos are first-party."""
        return self.kind is not AdKind.HOUSE

    def to_dict(self) -> dict[str, object]:
        return {
            "placement_id": self.placement_id,
            "kind": self.kind.value,
            "disclosure_label": self.disclosure_label,
            "disclosed": self.disclosed,
        }


__all__ = [
    "RES_COMMERCE",
    "AdKind",
    "BillingType",
    "Grant",
    "Offer",
    "OfferStatus",
    "PaymentCallbackEnvelope",
    "PaymentEventType",
    "Price",
    "Product",
    "Purchase",
    "PurchaseStatus",
    "SponsoredPlacement",
    "signing_payload",
]
