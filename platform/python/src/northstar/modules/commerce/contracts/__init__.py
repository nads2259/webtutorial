"""Commerce published contract identifiers (rule 40, LAW-11).

Commerce's public shapes derive from the canonical JSON Schemas in ``spec/contracts/schemas``:

* ``commerce-offer/1.0.0`` — an offer's structure (``commerce-offer.schema.json``); the domain
  :class:`~northstar.modules.commerce.domain.model.Offer` serialises to this shape via
  ``to_contract`` and a contract test validates it against the schema.
* ``entitlement-decision/1.0.0`` — the entitlement decision commerce reuses from the entitlement
  engine when granting/checking purchase entitlements (ARCH-019).

Contracts precede implementations and evolve additively (rule 40).
"""

from __future__ import annotations

CONTRACT_COMMERCE_OFFER = "commerce-offer/1.0.0"
CONTRACT_ENTITLEMENT_DECISION = "entitlement-decision/1.0.0"

COMMERCE_CONTRACTS: tuple[str, ...] = (
    CONTRACT_COMMERCE_OFFER,
    CONTRACT_ENTITLEMENT_DECISION,
)

__all__ = [
    "COMMERCE_CONTRACTS",
    "CONTRACT_COMMERCE_OFFER",
    "CONTRACT_ENTITLEMENT_DECISION",
]
