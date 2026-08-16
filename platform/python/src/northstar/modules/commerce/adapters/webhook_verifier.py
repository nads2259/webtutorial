"""HMAC payment-provider webhook verifier behind :class:`WebhookVerifierPort` (FR-COM-003).

A provider signs its callback by computing ``HMAC-SHA256(provider_key, signing_payload(envelope))``
where the signing material binds every business field (event id, type, provider, purchase, amount,
currency, time — see :func:`..domain.model.signing_payload`). The verifier holds ONLY the
per-provider verification keys — never an application credential — resolved from the secret manager
at the composition root (rule 50).

Verification fails CLOSED:

* an UNKNOWN provider -> ``False`` (never verified);
* a malformed / missing / forged signature, or one signed by any other key -> ``False``
  (constant-time comparison);
* because the signed material includes the amount and purchase id, TAMPERING with any field breaks
  the signature.

This reference adapter uses a symmetric HMAC key (like the extension signature verifier and the
simulation lease signer); a real provider (e.g. Stripe webhook signatures) is a drop-in swap behind
the same :class:`~..application.ports.WebhookVerifierPort` with no capability change.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping

from ..domain.model import PaymentCallbackEnvelope, signing_payload


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def sign_callback(envelope: PaymentCallbackEnvelope, key: bytes) -> str:
    """Produce the base64url signature for ``envelope`` (used by providers/tests, not a bus)."""
    return _b64e(_sign(key, signing_payload(envelope)))


class HmacWebhookVerifier:
    """Verifies payment-provider callback signatures against a trusted-provider key registry."""

    __slots__ = ("_providers",)

    def __init__(self, providers: Mapping[str, bytes]) -> None:
        self._providers = dict(providers)

    def verify(self, envelope: PaymentCallbackEnvelope) -> bool:
        key = self._providers.get(envelope.provider)
        if key is None:
            return False  # unknown provider — fail closed
        if not envelope.signature:
            return False  # unsigned — fail closed
        try:
            provided = _b64d(envelope.signature)
        except (ValueError, binascii.Error):
            return False  # malformed signature — fail closed
        expected = _sign(key, signing_payload(envelope))
        # Constant-time comparison: a forged / tampered signature is indistinguishable and rejected.
        return hmac.compare_digest(provided, expected)
