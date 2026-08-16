"""Pure RFC 6238 TOTP primitives (stdlib only — no infrastructure, docs/07 §3).

The one-time-password algorithm is a pure function of a shared secret and a time counter, so it
lives in the domain layer (rule 10): it imports only ``hmac``/``hashlib``/``base64``/``struct``/
``secrets`` and never touches persistence, HTTP or a provider SDK. Replay protection is a stateful
concern owned by an adapter/capability: :func:`verify_totp` accepts the ``last_used_step`` the
caller has persisted and never accepts a code from a step at or below it, so a code — once
accepted — cannot be reused (RFC 6238 §5.2). Validated against the RFC 6238 published test vectors.

References: RFC 4226 (HOTP), RFC 6238 (TOTP), RFC 6238 Appendix B (test vectors).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from collections.abc import Callable
from urllib.parse import quote, urlencode

DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
DEFAULT_ALGORITHM = "SHA1"

# RFC 6238 permits SHA-1/256/512; SHA-1 is the interoperable default for authenticator apps. TOTP's
# security derives from the shared secret + short validity window, not the hash's collision
# resistance, so SHA-1 here is not a weakness (it is the RFC-mandated interoperable baseline).
_ALGORITHMS: dict[str, Callable[[bytes], object]] = {
    "SHA1": hashlib.sha1,
    "SHA256": hashlib.sha256,
    "SHA512": hashlib.sha512,
}


def _digestmod(algorithm: str) -> Callable[[bytes], object]:
    try:
        return _ALGORITHMS[algorithm.upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported TOTP algorithm: {algorithm!r}") from exc


def generate_totp_secret(*, num_bytes: int = 20) -> str:
    """Return a fresh, high-entropy Base32 (RFC 4648, unpadded) TOTP shared secret.

    Twenty random bytes (160 bits) is the RFC 4226 recommended minimum; the Base32 encoding is
    the representation authenticator apps consume in the ``otpauth://`` provisioning URI.
    """
    if num_bytes < 16:
        raise ValueError("TOTP secret must be at least 16 bytes of entropy")
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    """Decode an unpadded, case-insensitive Base32 secret back to raw key bytes."""
    normalized = secret_b32.strip().replace(" ", "").upper()
    padding = "=" * (-len(normalized) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def hotp(
    secret_b32: str,
    counter: int,
    *,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Return the RFC 4226 HOTP value for ``counter`` as a zero-padded ``digits``-length string."""
    if counter < 0:
        raise ValueError("HOTP counter must be non-negative")
    key = _decode_secret(secret_b32)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, _digestmod(algorithm)).digest()
    offset = digest[-1] & 0x0F
    truncated = (
        (digest[offset] & 0x7F) << 24
        | (digest[offset + 1] & 0xFF) << 16
        | (digest[offset + 2] & 0xFF) << 8
        | (digest[offset + 3] & 0xFF)
    )
    return str(truncated % (10**digits)).zfill(digits)


def timestep(timestamp: int, *, period: int = DEFAULT_PERIOD, t0: int = 0) -> int:
    """Return the RFC 6238 time-step counter ``T = floor((now - T0) / period)``."""
    if period <= 0:
        raise ValueError("TOTP period must be positive")
    return (int(timestamp) - t0) // period


def generate_totp(
    secret_b32: str,
    timestamp: int,
    *,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
    t0: int = 0,
) -> str:
    """Return the TOTP code valid at ``timestamp`` (seconds since the Unix epoch)."""
    counter = timestep(timestamp, period=period, t0=t0)
    return hotp(secret_b32, counter, digits=digits, algorithm=algorithm)


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    timestamp: int,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
    window: int = 1,
    last_used_step: int | None = None,
    t0: int = 0,
) -> int | None:
    """Verify ``code`` against a small ±``window`` step window with replay protection.

    Returns the matching time-step counter on success (which the caller MUST persist as the new
    ``last_used_step``), or ``None`` if no step in the window matches. A step at or below
    ``last_used_step`` is never accepted, so an already-consumed code — or any earlier code —
    cannot be replayed (RFC 6238 §5.2). Comparison is constant-time (``hmac.compare_digest``).
    """
    if window < 0:
        raise ValueError("TOTP window must be non-negative")
    candidate = (code or "").strip()
    if not candidate.isdigit() or len(candidate) != digits:
        return None
    current = timestep(timestamp, period=period, t0=t0)
    for step in range(current - window, current + window + 1):
        if step < 0:
            continue
        if last_used_step is not None and step <= last_used_step:
            continue
        expected = hotp(secret_b32, step, digits=digits, algorithm=algorithm)
        if hmac.compare_digest(expected, candidate):
            return step
    return None


def provisioning_uri(
    secret_b32: str,
    *,
    account_name: str,
    issuer: str,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Build the ``otpauth://totp`` provisioning URI an authenticator app imports (KeyURI format).

    The label is ``issuer:account_name`` (both percent-encoded); the query carries the shared
    secret plus the digits/period/algorithm so the app derives identical codes.
    """
    if not issuer:
        raise ValueError("provisioning issuer must be non-empty")
    if not account_name:
        raise ValueError("provisioning account_name must be non-empty")
    label = f"{quote(issuer)}:{quote(account_name)}"
    query = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": algorithm.upper(),
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{query}"
