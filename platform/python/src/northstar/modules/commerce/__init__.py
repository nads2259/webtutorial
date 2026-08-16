"""Commerce module: offers/products, purchases, signed payment callbacks, refunds, ad disclosure.

Owns the ``northstar_commerce`` schema (docs/29, FR-COM-001..005). Offers compose free/paid/tier
access and purchasing grants entitlements by REUSING the existing entitlement engine (never by
branching on plan/payment-provider names — ARCH-019). Payment provider callbacks are verified
against a signature behind a :class:`WebhookVerifierPort` and processed idempotently; a
forged/unsigned/tampered/replayed callback is rejected fail-closed and never mutates entitlements.
Refunds revoke the granted entitlement idempotently and auditably. Sponsored/advertising surfaces
are always flagged disclosed.
"""
