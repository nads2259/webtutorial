"""HMAC-signed lease issuer + validator (FR-SIM-004).

The control plane issues a short-lived lease and signs its canonical claim set with HMAC-SHA256
(reusing a KMS-resolvable key, like the crypto adapter's KEK). The token is opaque and
self-contained::

    base64url(canonical(claims)) . base64url(HMAC-SHA256(key, canonical(claims)))

The executor side validates the token WITHOUT any application credential: it recomputes the HMAC in
constant time and reconstructs the :class:`Lease`. A forged/tampered token (or one signed by any
other key) fails the constant-time comparison and raises :class:`LeaseInvalid` — nothing runs. The
expiry and over-broad-scope checks are applied by the run capability against the authoritative clock
and policy; this adapter proves ONLY authenticity + integrity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime

from ..application.ports import SignedLease
from ..domain.errors import LeaseInvalid
from ..domain.model import Lease, ResourceQuota, RuntimeTier, lease_signing_payload


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class HmacLeaseSigner:
    """Issues and validates HMAC-SHA256 signed leases (implements issuer + validator ports)."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("lease signing key must be at least 16 bytes")
        self._key = key

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def issue(self, lease: Lease) -> SignedLease:
        payload = lease_signing_payload(lease)
        signature = self._sign(payload)
        token = f"{_b64e(payload)}.{_b64e(signature)}"
        return SignedLease(lease=lease, token=token)

    def validate(self, token: str) -> Lease:
        try:
            encoded_payload, encoded_sig = token.split(".", 1)
            payload = _b64d(encoded_payload)
            signature = _b64d(encoded_sig)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise LeaseInvalid("forged", "lease token is malformed") from exc

        expected = self._sign(payload)
        # Constant-time comparison: a wrong/forged signature is indistinguishable and rejected.
        if not hmac.compare_digest(signature, expected):
            raise LeaseInvalid("forged", "lease signature is invalid")

        try:
            claims = json.loads(payload.decode("utf-8"))
            lease = _lease_from_claims(claims)
        except (ValueError, KeyError, TypeError) as exc:
            raise LeaseInvalid("forged", "lease claims are malformed") from exc

        # Defense-in-depth: the reconstructed lease must re-sign to the same canonical payload,
        # so a token whose claims were reordered/altered to match a stale signature is still caught.
        if lease_signing_payload(lease) != payload:
            raise LeaseInvalid("forged", "lease claims do not match the signed payload")
        return lease


def _lease_from_claims(claims: dict[str, object]) -> Lease:
    return Lease(
        lease_id=str(claims["lease_id"]),
        simulation_id=str(claims["simulation_id"]),
        version=str(claims["version"]),
        definition_hash=str(claims["definition_hash"]),
        organization_id=str(claims["organization_id"]),
        subject_id=str(claims["subject_id"]),
        tier=RuntimeTier(str(claims["tier"])),
        egress_allowlist=tuple(str(d) for d in claims["egress_allowlist"]),  # type: ignore[union-attr]
        quota=ResourceQuota.from_dict(claims["quota"]),  # type: ignore[arg-type]
        issued_at=datetime.fromisoformat(str(claims["issued_at"])),
        expires_at=datetime.fromisoformat(str(claims["expires_at"])),
        nonce=str(claims["nonce"]),
    )
