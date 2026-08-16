"""Ports (abstractions) and DTOs for the identity application layer (rule 10/20).

Every collaborator the capabilities need is expressed as a small, role-specific Protocol so the
application never depends on a concrete provider, database or MFA library (ISP + DIP). Adapters in
:mod:`..adapters` implement these; federation (OIDC IdP) and SCIM provisioning are ports here —
not identity-core forks — satisfying FR-IDN-006.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.mfa import TotpCredential, WebAuthnCredential
from ..domain.model import (
    Credential,
    ExternalIdentity,
    MfaFactorType,
    Session,
    Subject,
    User,
)


class OidcProviderError(Exception):
    """The OIDC provider could not exchange the authorization code (adapter-level failure).

    Raised by :meth:`OidcProviderPort.exchange_code` for an unknown/expired code or a failed
    PKCE binding. The capability catches it and re-raises a uniform ``AuthenticationFailed`` so
    callers cannot distinguish failure causes (anti-enumeration, docs/07 §14).
    """


# ---------------------------------------------------------------------------
# OIDC / OAuth (Authorization Code + PKCE) provider port  (FR-IDN-002/006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Parameters the browser is redirected with (OAuth Authorization Code + PKCE)."""

    authorization_url: str
    state: str
    nonce: str
    code_challenge: str
    code_challenge_method: str


@dataclass(frozen=True, slots=True)
class IdTokenClaims:
    """The validated claims an ID token carries after code exchange (OIDC Core §2)."""

    issuer: str
    audience: str
    subject: str
    nonce: str | None
    email: str | None = None
    email_verified: bool = False
    extra: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class OidcProviderPort(Protocol):
    """A federated OIDC/OAuth identity provider (adapter, FR-IDN-006).

    Implementations build the authorization redirect and exchange the authorization ``code``
    (with the PKCE ``code_verifier``) for validated :class:`IdTokenClaims`. The exchange MUST
    verify the PKCE binding; issuer/audience/nonce validation is performed by the capability.
    """

    @property
    def issuer(self) -> str: ...

    @property
    def audience(self) -> str: ...

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        code_challenge_method: str,
        scopes: tuple[str, ...],
    ) -> str: ...

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> IdTokenClaims: ...


# ---------------------------------------------------------------------------
# Pending-authorization (state/nonce/PKCE) server-side store
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthTransaction:
    """Server-side record correlating a redirect with its PKCE verifier/nonce (one-time use)."""

    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime


@runtime_checkable
class AuthTransactionStorePort(Protocol):
    """Stores pending authorization transactions keyed by ``state`` (single-use)."""

    def save(self, transaction: AuthTransaction) -> None: ...

    def pop(self, state: str) -> AuthTransaction | None:
        """Return and delete the transaction for ``state`` (or ``None`` if absent/consumed)."""
        ...


# ---------------------------------------------------------------------------
# Identity directory (subjects + users + external-identity linking)  (FR-IDN-001)
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityDirectoryPort(Protocol):
    """Persists subjects/users and resolves/provisions them by external identity (JIT)."""

    def find_by_external_identity(
        self, identity: ExternalIdentity
    ) -> tuple[Subject, User] | None: ...

    def provision(
        self,
        *,
        identity: ExternalIdentity,
        email: str | None,
        display_name: str | None,
        tenant_scope: str | None,
    ) -> tuple[Subject, User]:
        """Create a subject+user linked to ``identity`` (never auto-granting roles, docs/07 §3)."""
        ...

    def add_subject(self, subject: Subject) -> None: ...


# ---------------------------------------------------------------------------
# Session store (server-managed, opaque hashed session id)  (FR-IDN-003)
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionStorePort(Protocol):
    """Persists sessions by the *hash* of their opaque token (never the raw token, docs/07 §4)."""

    def create(self, *, raw_token: str, session: Session) -> None: ...

    def authenticate(self, *, raw_token: str, now: datetime) -> Session | None:
        """Return the active session for ``raw_token`` (hashing internally) or ``None``."""
        ...

    def get(self, session_id: str) -> Session | None: ...

    def replace(self, session: Session) -> None:
        """Persist an updated session value (rotation, revocation, idle refresh)."""
        ...


# ---------------------------------------------------------------------------
# MFA / WebAuthn-passkey ports  (FR-IDN-004)
# ---------------------------------------------------------------------------


@runtime_checkable
class MfaEnrollmentPort(Protocol):
    """Registers an authentication factor (passkeys preferred; full WebAuthn is an adapter)."""

    def enroll(
        self,
        *,
        subject_id: str,
        factor_type: MfaFactorType,
        material: Mapping[str, object],
    ) -> Credential: ...


@runtime_checkable
class MfaVerificationPort(Protocol):
    """Verifies a presented authentication factor (passkey assertion, TOTP code, …)."""

    def verify(
        self,
        *,
        subject_id: str,
        factor_type: MfaFactorType,
        proof: Mapping[str, object],
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Real MFA persistence + verifier ports (TOTP + WebAuthn)  (FR-IDN-003)
# ---------------------------------------------------------------------------


@runtime_checkable
class TotpCredentialStorePort(Protocol):
    """Persists a subject's TOTP secret and its monotonic replay-protection cursor.

    A subject has at most one active TOTP credential. The store owns the ``last_used_step`` so a
    consumed code — persisted as the new cursor — can never be replayed (RFC 6238 §5.2).
    """

    def save(self, credential: TotpCredential) -> None: ...

    def get(self, subject_id: str) -> TotpCredential | None: ...

    def replace(self, credential: TotpCredential) -> None:
        """Persist an advanced credential (confirmed / new ``last_used_step``)."""
        ...

    def delete_for_subject(self, subject_id: str) -> None: ...


@runtime_checkable
class WebAuthnCredentialStorePort(Protocol):
    """Persists WebAuthn/passkey credentials (COSE public key + signature counter)."""

    def save(self, credential: WebAuthnCredential) -> None: ...

    def get(self, *, credential_id: str) -> WebAuthnCredential | None: ...

    def list_for_subject(self, subject_id: str) -> tuple[WebAuthnCredential, ...]: ...

    def set_sign_count(self, *, credential_id: str, sign_count: int) -> None: ...

    def delete_for_subject(self, subject_id: str) -> None: ...


@runtime_checkable
class WebAuthnChallengeStorePort(Protocol):
    """Stores the single-use WebAuthn challenge issued for a ceremony, keyed by subject.

    The challenge is popped on verification so a captured registration/assertion cannot be
    replayed against a stale challenge (WebAuthn §13.4.3).
    """

    def save(self, *, subject_id: str, ceremony: str, challenge: bytes) -> None: ...

    def pop(self, *, subject_id: str, ceremony: str) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class WebAuthnRegistrationVerification:
    """The verified result of a WebAuthn registration (attestation) ceremony."""

    credential_id: str
    public_key: bytes
    sign_count: int
    aaguid: str | None = None


@runtime_checkable
class WebAuthnVerifierPort(Protocol):
    """Builds WebAuthn options and verifies attestation/assertion (py_webauthn is an adapter).

    Implementations MUST check the origin and RP id and MUST reject a signature-count regression
    on authentication (a cloned authenticator, WebAuthn §6.1.1), raising ``MfaVerificationFailed``.
    """

    @property
    def rp_id(self) -> str: ...

    @property
    def origin(self) -> str: ...

    def build_registration_options(
        self, *, subject_id: str, user_name: str, existing_credential_ids: tuple[str, ...]
    ) -> tuple[str, bytes]:
        """Return ``(options_json, challenge)`` for ``navigator.credentials.create()``."""
        ...

    def verify_registration(
        self, *, response: Mapping[str, object], expected_challenge: bytes
    ) -> WebAuthnRegistrationVerification: ...

    def build_authentication_options(
        self, *, allow_credential_ids: tuple[str, ...]
    ) -> tuple[str, bytes]:
        """Return ``(options_json, challenge)`` for ``navigator.credentials.get()``."""
        ...

    def verify_authentication(
        self,
        *,
        response: Mapping[str, object],
        expected_challenge: bytes,
        credential_public_key: bytes,
        current_sign_count: int,
    ) -> int:
        """Return the new signature counter; raise on a sign-count regression."""
        ...


# ---------------------------------------------------------------------------
# Federation + SCIM provisioning ports  (FR-IDN-006 — ports, not core forks)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScimUserResource:
    """A minimal SCIM 2.0 user resource for enterprise provisioning (RFC 7643 subset)."""

    external_id: str
    user_name: str
    active: bool
    email: str | None = None
    display_name: str | None = None


@runtime_checkable
class ScimProvisioningPort(Protocol):
    """Enterprise SCIM provisioning adapter (JIT must not auto-grant privileged roles)."""

    def provision(self, resource: ScimUserResource) -> tuple[Subject, User]: ...

    def deprovision(self, external_id: str) -> None: ...
