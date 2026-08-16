"""PKCE (RFC 7636) and anti-forgery primitives — stdlib only (no new dependency).

Browser authentication uses OAuth 2.0 Authorization Code with PKCE (docs/07 §3, rule 50). This
module derives the ``code_verifier``/``code_challenge`` pair and the ``state``/``nonce``
anti-forgery values using only :mod:`secrets` (CSPRNG), :mod:`hashlib` (SHA-256) and
:mod:`base64` (URL-safe, unpadded) — deliberately avoiding a third-party OIDC/PKCE dependency.

The ``S256`` challenge method is mandatory (``plain`` is not offered): a challenge is the
URL-safe, unpadded base64 of ``SHA-256(ascii(code_verifier))``, per RFC 7636 §4.2.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

CODE_CHALLENGE_METHOD = "S256"

# RFC 7636 §4.1: the verifier is 43-128 chars of the unreserved set. token_urlsafe(32) yields a
# 43-char string drawn from that set, satisfying the minimum with full 256 bits of entropy.
_VERIFIER_ENTROPY_BYTES = 32
CODE_VERIFIER_MIN_LENGTH = 43
CODE_VERIFIER_MAX_LENGTH = 128


def _b64url_no_pad(raw: bytes) -> str:
    """URL-safe base64 without ``=`` padding (RFC 7636 uses base64url, no padding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_code_verifier() -> str:
    """Return a fresh high-entropy PKCE ``code_verifier`` (RFC 7636 §4.1)."""
    return secrets.token_urlsafe(_VERIFIER_ENTROPY_BYTES)


def compute_code_challenge(code_verifier: str) -> str:
    """Return the ``S256`` ``code_challenge`` for ``code_verifier`` (RFC 7636 §4.2).

    Raises :class:`ValueError` when the verifier is outside the RFC-mandated length window.
    """
    if not (CODE_VERIFIER_MIN_LENGTH <= len(code_verifier) <= CODE_VERIFIER_MAX_LENGTH):
        raise ValueError(
            "code_verifier length must be between "
            f"{CODE_VERIFIER_MIN_LENGTH} and {CODE_VERIFIER_MAX_LENGTH} characters"
        )
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return _b64url_no_pad(digest)


def verify_code_challenge(code_verifier: str, code_challenge: str) -> bool:
    """Constant-time check that ``code_verifier`` produced ``code_challenge`` (S256)."""
    try:
        expected = compute_code_challenge(code_verifier)
    except ValueError:
        return False
    return hmac.compare_digest(expected, code_challenge)


def generate_state() -> str:
    """Return an unguessable OAuth ``state`` value (CSRF defense for the redirect, RFC 9700)."""
    return secrets.token_urlsafe(_VERIFIER_ENTROPY_BYTES)


def generate_nonce() -> str:
    """Return an unguessable OIDC ``nonce`` (replay defense binding the ID token to the request)."""
    return secrets.token_urlsafe(_VERIFIER_ENTROPY_BYTES)


@dataclass(frozen=True, slots=True)
class PkceChallenge:
    """A PKCE verifier/challenge pair and its method (always ``S256``)."""

    code_verifier: str
    code_challenge: str
    code_challenge_method: str = CODE_CHALLENGE_METHOD


def create_pkce_challenge() -> PkceChallenge:
    """Create a fresh :class:`PkceChallenge` (verifier + S256 challenge)."""
    verifier = generate_code_verifier()
    return PkceChallenge(
        code_verifier=verifier,
        code_challenge=compute_code_challenge(verifier),
        code_challenge_method=CODE_CHALLENGE_METHOD,
    )
