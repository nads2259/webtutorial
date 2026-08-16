"""Deterministic in-memory mock OIDC/OAuth provider for tests (no network, no new dependency).

Plays the role of a federated IdP so the full Authorization Code + PKCE flow can be exercised
end to end without a live provider (rule 50 mandates a deterministic mock for tests). It builds a
standards-shaped authorization URL, and — via :meth:`issue_code`, which simulates the IdP after
user login — mints an authorization code bound to the request's PKCE ``code_challenge`` and
``nonce``. :meth:`exchange_code` then enforces the PKCE binding before returning ID-token claims.

``issue_code`` accepts optional issuer/audience/nonce overrides so negative tests can simulate a
mis-issued token (issuer/audience/nonce mismatch) and assert the capability rejects it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from ..application.ports import IdTokenClaims, OidcProviderError
from ..domain.pkce import CODE_CHALLENGE_METHOD, verify_code_challenge


@dataclass(frozen=True, slots=True)
class _PendingCode:
    code_challenge: str
    code_challenge_method: str
    nonce: str | None
    subject: str
    issuer: str
    audience: str
    email: str | None
    email_verified: bool


class MockOidcProvider:
    """A deterministic, in-memory OIDC provider implementing :class:`OidcProviderPort`."""

    def __init__(
        self,
        *,
        issuer: str = "https://mock-idp.northstar.local",
        audience: str = "northstar-web",
        authorization_endpoint: str = "https://mock-idp.northstar.local/authorize",
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._authorization_endpoint = authorization_endpoint
        self._codes: dict[str, _PendingCode] = {}

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        code_challenge_method: str,
        scopes: tuple[str, ...],
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self._audience,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }
        return f"{self._authorization_endpoint}?{urlencode(params)}"

    def issue_code(
        self,
        *,
        code_challenge: str,
        nonce: str | None,
        subject: str,
        code_challenge_method: str = CODE_CHALLENGE_METHOD,
        issuer: str | None = None,
        audience: str | None = None,
        email: str | None = None,
        email_verified: bool = True,
    ) -> str:
        """Simulate the IdP issuing an authorization code after a successful user login.

        Overriding ``issuer``/``audience``/``nonce`` lets negative tests forge a mis-issued token.
        """
        code = secrets.token_urlsafe(24)
        self._codes[code] = _PendingCode(
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            nonce=nonce,
            subject=subject,
            issuer=issuer if issuer is not None else self._issuer,
            audience=audience if audience is not None else self._audience,
            email=email,
            email_verified=email_verified,
        )
        return code

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> IdTokenClaims:
        pending = self._codes.pop(code, None)
        if pending is None:
            raise OidcProviderError("unknown or replayed authorization code")
        if pending.code_challenge_method != CODE_CHALLENGE_METHOD:
            raise OidcProviderError("unsupported PKCE code_challenge_method")
        if not verify_code_challenge(code_verifier, pending.code_challenge):
            raise OidcProviderError("PKCE verification failed")
        return IdTokenClaims(
            issuer=pending.issuer,
            audience=pending.audience,
            subject=pending.subject,
            nonce=pending.nonce,
            email=pending.email,
            email_verified=pending.email_verified,
        )
