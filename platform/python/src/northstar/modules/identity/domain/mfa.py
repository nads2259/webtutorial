"""MFA credential value objects (pure domain, docs/07 §3).

These frozen records model the *stored* material for a second factor without importing any
infrastructure (rule 10): a :class:`TotpCredential` carries the Base32 shared secret plus its
digits/period/algorithm and the monotonically-advancing ``last_used_step`` that enforces replay
protection; a :class:`WebAuthnCredential` carries the COSE public key and the signature counter
whose regression a verifier must reject (WebAuthn §6.1.1). The secret/public-key material is data
the persistence adapter is responsible for protecting at rest (KMS-wrapped in production).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .errors import SessionInvariantViolation


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None:
        raise SessionInvariantViolation(f"{field} must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class TotpCredential:
    """A registered RFC 6238 TOTP authenticator secret and its replay-protection cursor."""

    credential_id: str
    subject_id: str
    secret: str
    digits: int
    period: int
    algorithm: str
    created_at: datetime
    confirmed_at: datetime | None = None
    last_used_step: int | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.credential_id:
            raise ValueError("totp credential_id must be non-empty")
        if not self.subject_id:
            raise ValueError("totp credential subject_id must be non-empty")
        if not self.secret:
            raise ValueError("totp secret must be non-empty")
        if self.digits < 6 or self.digits > 10:
            raise ValueError("totp digits must be between 6 and 10")
        if self.period <= 0:
            raise ValueError("totp period must be positive")
        _require_utc(self.created_at, "totp.created_at")

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def confirmed(self, *, step: int, now: datetime) -> TotpCredential:
        """Return a copy advanced to ``step`` and marked confirmed (first successful verify)."""
        _require_utc(now, "totp.confirmed_at")
        return replace(self, last_used_step=step, confirmed_at=self.confirmed_at or now)


@dataclass(frozen=True, slots=True)
class WebAuthnCredential:
    """A registered WebAuthn/passkey credential (COSE public key + signature counter)."""

    credential_id: str
    subject_id: str
    public_key: bytes
    sign_count: int
    rp_id: str
    origin: str
    created_at: datetime
    aaguid: str | None = None
    transports: tuple[str, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.credential_id:
            raise ValueError("webauthn credential_id must be non-empty")
        if not self.subject_id:
            raise ValueError("webauthn credential subject_id must be non-empty")
        if not self.public_key:
            raise ValueError("webauthn public_key must be non-empty")
        if self.sign_count < 0:
            raise ValueError("webauthn sign_count must be non-negative")
        _require_utc(self.created_at, "webauthn.created_at")

    def with_sign_count(self, sign_count: int) -> WebAuthnCredential:
        """Return a copy advanced to ``sign_count`` (must be strictly monotonic when non-zero)."""
        if sign_count < 0:
            raise ValueError("webauthn sign_count must be non-negative")
        return replace(self, sign_count=sign_count)
