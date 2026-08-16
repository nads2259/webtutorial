"""HMAC signature + provenance verifier over a trusted-publisher key registry (FR-EXT-004).

A publisher signs its extension by computing ``HMAC-SHA256(publisher_key, canonical(signing
material))`` where the signing material binds the plugin id, version, publisher, package digest,
provenance and SBOM (see :func:`..domain.model.signing_material`). The verifier holds ONLY the
per-publisher verification keys (and each publisher's review-assigned trust tier) — never an
application credential.

Verification fails CLOSED (EVAL-SEC-009, supply chain):

* an UNKNOWN or UNVERIFIED publisher -> :class:`UntrustedPublisher`;
* a signature that does not recompute (forged, or signed by any other key) ->
  :class:`SignatureForged` (constant-time comparison);
* (the tamper + unsigned checks run in the capability before verification).

Because the signed material includes the package digest, provenance and SBOM, tampering with any of
them breaks the signature. This reference adapter uses a symmetric HMAC key (like the simulation
lease signer); a production deployment swaps in an asymmetric/ Sigstore-backed verifier behind the
same :class:`~..application.ports.SignatureVerifierPort` with no capability change.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from ..application.ports import TrustAssertion
from ..domain.errors import SignatureForged, UntrustedPublisher
from ..domain.model import ExtensionManifest, TrustTier, signing_payload


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True, slots=True)
class PublisherKey:
    """A trusted publisher's verification key + review-assigned trust tier (never self-declared)."""

    key: bytes
    granted_trust_tier: TrustTier
    verified: bool = True


def _sign(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def sign_manifest(manifest: ExtensionManifest, key: bytes) -> str:
    """Produce the base64url signature for ``manifest`` (used by publishers/tests, not a bus)."""
    return _b64e(_sign(key, signing_payload(manifest)))


class HmacSignatureVerifier:
    """Verifies extension signatures against a trusted-publisher key registry (fail closed)."""

    __slots__ = ("_publishers",)

    def __init__(self, publishers: Mapping[str, PublisherKey]) -> None:
        self._publishers = dict(publishers)

    def is_verified_publisher(self, publisher_id: str) -> bool:
        record = self._publishers.get(publisher_id)
        return record is not None and record.verified

    def verify(self, manifest: ExtensionManifest) -> TrustAssertion:
        record = self._publishers.get(manifest.publisher_id)
        if record is None or not record.verified:
            raise UntrustedPublisher(manifest.publisher_id)

        try:
            provided = _b64d(manifest.artifacts.signature)
        except (ValueError, binascii.Error) as exc:
            raise SignatureForged("extension signature is malformed") from exc

        expected = _sign(record.key, signing_payload(manifest))
        # Constant-time comparison: a wrong/forged signature is indistinguishable and rejected.
        if not hmac.compare_digest(provided, expected):
            raise SignatureForged()

        return TrustAssertion(
            publisher_id=manifest.publisher_id,
            granted_trust_tier=record.granted_trust_tier,
            verified=True,
        )
