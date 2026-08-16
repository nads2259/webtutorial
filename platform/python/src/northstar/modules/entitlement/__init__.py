"""Entitlement module: commercial grants and entitlement decisions (docs/07 §8, ARCH-019).

Entitlements are represented as *grants* (subject/organization scope, capability scope, quota,
validity, origin, revocation). The domain answers entitlement *decisions*; it never branches on a
plan or payment-provider name (ARCH-019, FR-POL-005) — only on grant origin *types* and quotas.
The module owns the ``northstar_entitlement`` schema (LAW-13).
"""
