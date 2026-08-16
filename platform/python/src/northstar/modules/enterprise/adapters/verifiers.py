"""Signature-verified reference federation + LTI verifiers behind their ports (rule 50).

An external IdP (federation) and an LMS (LTI) sign their assertion/launch by computing
``HMAC-SHA256(issuer_key, signing_payload(...))`` over the canonical material defined in the
domain (:func:`federation_signing_payload` / :func:`lti_signing_payload`). Both verifiers hold ONLY
the per-issuer verification keys — never an application credential — resolved from the secret
manager at the composition root.

Verification fails CLOSED (returns ``None``/``False``, never raises on a bad input):

* an UNKNOWN issuer -> rejected (no key, never verified);
* a malformed / missing / forged signature, or one signed by any other key -> rejected
  (constant-time comparison);
* an EXPIRED assertion/launch (outside its validity window) -> rejected;
* because the signed material binds every trust-relevant field, TAMPERING breaks the signature.

These are reference adapters using a symmetric HMAC key (like the commerce webhook verifier and the
simulation lease signer). A real OIDC/SAML JWKS verifier or LTI 1.3 platform-key verifier is a
drop-in swap behind :class:`FederationVerifierPort` / :class:`LtiVerifierPort` with no capability
change (non-scope: real provider network integration).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime

from ..domain.model import (
    FederationAssertion,
    LtiLaunch,
    VerifiedFederationClaims,
    federation_signing_payload,
    lti_signing_payload,
)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def _matches(key: bytes, payload: bytes, signature: str) -> bool:
    if not signature:
        return False  # unsigned — fail closed
    try:
        provided = _b64d(signature)
    except (ValueError, binascii.Error):
        return False  # malformed — fail closed
    # Constant-time comparison: a forged / tampered signature is indistinguishable and rejected.
    return hmac.compare_digest(provided, _sign(key, payload))


def sign_federation_assertion(assertion: FederationAssertion, key: bytes) -> str:
    """Produce the base64url signature for ``assertion`` (used by an IdP/tests, not a bus)."""
    return _b64e(_sign(key, federation_signing_payload(assertion)))


def sign_lti_launch(launch: LtiLaunch, key: bytes) -> str:
    """Produce the base64url signature for ``launch`` (used by an LMS platform/tests, not a bus)."""
    return _b64e(_sign(key, lti_signing_payload(launch)))


class HmacFederationVerifier:
    """Verifies a federated IdP assertion against a trusted-issuer key registry (fail-closed)."""

    __slots__ = ("_audience", "_issuers")

    def __init__(self, issuers: Mapping[str, bytes], *, audience: str) -> None:
        self._issuers = dict(issuers)
        self._audience = audience

    def verify(
        self, assertion: FederationAssertion, *, now: datetime
    ) -> VerifiedFederationClaims | None:
        key = self._issuers.get(assertion.issuer)
        if key is None:
            return None  # unknown issuer — fail closed
        if assertion.audience != self._audience:
            return None  # wrong audience — fail closed
        if not assertion.is_within_validity(now):
            return None  # expired / not-yet-valid — fail closed
        if not _matches(key, federation_signing_payload(assertion), assertion.signature):
            return None  # forged / tampered / unsigned — fail closed
        return VerifiedFederationClaims(
            issuer=assertion.issuer,
            subject=assertion.subject,
            audience=assertion.audience,
            email=assertion.email,
            display_name=assertion.display_name,
        )


class HmacLtiVerifier:
    """Verifies a signed LTI launch against a trusted-platform key registry (fail-closed)."""

    __slots__ = ("_platforms",)

    def __init__(self, platforms: Mapping[str, bytes]) -> None:
        self._platforms = dict(platforms)

    def verify(self, launch: LtiLaunch, *, now: datetime) -> bool:
        key = self._platforms.get(launch.issuer)
        if key is None:
            return False  # unknown platform — fail closed
        if not launch.is_within_validity(now):
            return False  # expired — fail closed
        return _matches(key, lti_signing_payload(launch), launch.signature)
