"""Typed commerce domain errors (rule 30/40): explainable, deterministic diagnostics.

The commerce domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary (rule 40). The kernel error base carries the
structured diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class CommerceError(KernelError):
    """Base class for commerce domain errors."""


class OfferValidationError(CommerceError):
    """An offer/product violates ``commerce-offer.schema.json`` invariants (FR-COM-001/002).

    Deny-by-default: an offer that omits a price, a grant, or a terms version — or that carries a
    malformed price/grant — is rejected and never published.
    """

    def __init__(self, message: str, code: str = "commerce.offer.invalid") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class CallbackRejected(CommerceError):  # noqa: N818 canonical error name
    """A payment provider callback failed signature verification or replay checks (FR-COM-003).

    Fail-closed: a forged / unsigned / tampered / replayed callback is REJECTED and never mutates
    entitlements. The message is deliberately generic so it never discloses signing material.
    """

    def __init__(self, reason: str = "callback_rejected") -> None:
        message = "payment callback rejected: signature verification failed (fail closed)"
        super().__init__(message, (Diagnostic(code="commerce.callback.rejected", message=reason),))
        self.reason = reason


class PurchaseNotFound(CommerceError):  # noqa: N818 canonical error name
    """A referenced purchase does not exist in this tenant (deny-by-default)."""

    def __init__(self, purchase_id: str) -> None:
        message = f"purchase {purchase_id!r} was not found"
        super().__init__(
            message, (Diagnostic(code="commerce.purchase.not_found", message=message),)
        )
        self.purchase_id = purchase_id


class OfferNotFound(CommerceError):  # noqa: N818 canonical error name
    """A referenced offer/version does not exist in this tenant (deny-by-default)."""

    def __init__(self, offer_id: str) -> None:
        message = f"offer {offer_id!r} was not found"
        super().__init__(message, (Diagnostic(code="commerce.offer.not_found", message=message),))
        self.offer_id = offer_id


class AdDisclosureRequired(CommerceError):  # noqa: N818 canonical error name
    """A sponsored/advertising surface was constructed without a disclosure (FR-COM-005)."""

    def __init__(self) -> None:
        message = "a sponsored/advertising surface MUST be flagged disclosed with a label"
        super().__init__(
            message, (Diagnostic(code="commerce.ad.disclosure_required", message=message),)
        )


class TenantScopeMissing(CommerceError):  # noqa: N818 canonical error name
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        message = "a tenant scope is required for this operation"
        super().__init__(message, (Diagnostic(code="commerce.tenant.missing", message=message),))
