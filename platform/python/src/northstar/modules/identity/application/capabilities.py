"""Identity capabilities: the one authoritative implementation per action (LAW-04).

Each capability is a :class:`~northstar.kernel.capabilities.registry.CapabilityHandler` invoked
through the kernel command/query bus, so every authentication/session mutation is authorized
deny-by-default and recorded as tamper-evident audit evidence (rule 50, LAW-14). The handlers
depend only on the ports in :mod:`.ports` and the pure :mod:`..domain`; concrete adapters are
injected at construction (rule 20 §D).

Capabilities:

* ``identity.subject.register`` — create a security principal.
* ``identity.authentication.begin`` — build the OAuth Authorization-Code + PKCE redirect and
  persist the pending transaction (state/nonce/verifier) server-side.
* ``identity.authentication.complete`` — validate the callback (state/nonce/issuer/audience),
  JIT-provision the subject/user and mint a server-managed session. Uniform failures resist
  account/token enumeration (docs/07 §14).
* ``identity.session.rotate`` — rotate to a fresh session id on privilege change (old revoked).
* ``identity.session.revoke`` — explicit revocation / logout (uniform result).
* ``identity.session.describe`` (query) — read the active session for a protected request.
* ``identity.mfa.enroll`` / ``identity.mfa.verify`` — passkey-preferred MFA ports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.errors import (
    AuthenticationFailed,
    MfaVerificationFailed,
    SessionNotAuthenticated,
)
from ..domain.model import (
    AssuranceLevel,
    ExternalIdentity,
    MfaFactorType,
    Session,
    Subject,
    SubjectType,
)
from ..domain.pkce import create_pkce_challenge, generate_nonce, generate_state
from .ports import (
    AuthTransaction,
    AuthTransactionStorePort,
    IdentityDirectoryPort,
    MfaEnrollmentPort,
    MfaVerificationPort,
    OidcProviderError,
    OidcProviderPort,
    SessionStorePort,
)

CAP_VERSION = "1.0.0"

SESSION_RESOURCE_TYPE = "identity.session"

CAP_REGISTER_SUBJECT = "identity.subject.register"
CAP_BEGIN_AUTHENTICATION = "identity.authentication.begin"
CAP_COMPLETE_AUTHENTICATION = "identity.authentication.complete"
CAP_ROTATE_SESSION = "identity.session.rotate"
CAP_REVOKE_SESSION = "identity.session.revoke"
CAP_DESCRIBE_SESSION = "identity.session.describe"
CAP_ENROLL_MFA = "identity.mfa.enroll"
CAP_VERIFY_MFA = "identity.mfa.verify"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
TokenFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Idle and absolute session lifetimes, configurable by risk profile (docs/07 §3)."""

    idle_ttl: timedelta = timedelta(hours=1)
    absolute_ttl: timedelta = timedelta(hours=12)


_DEFAULT_SESSION_POLICY = SessionPolicy()


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterSubjectCommand:
    subject_type: SubjectType
    tenant_scope: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterSubjectResult:
    subject_id: str
    subject_type: SubjectType


@dataclass(frozen=True, slots=True)
class BeginAuthenticationCommand:
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid",)


@dataclass(frozen=True, slots=True)
class BeginAuthenticationResult:
    authorization_url: str
    state: str


@dataclass(frozen=True, slots=True)
class CompleteAuthenticationCommand:
    code: str
    state: str
    state_cookie: str | None


@dataclass(frozen=True, slots=True)
class CompleteAuthenticationResult:
    raw_session_token: str
    session_id: str
    subject_id: str
    assurance: AssuranceLevel
    absolute_expires_at: datetime
    provisioned: bool


@dataclass(frozen=True, slots=True)
class RotateSessionCommand:
    session_id: str
    reason: str
    raise_to: AssuranceLevel | None = None


@dataclass(frozen=True, slots=True)
class RotateSessionResult:
    raw_session_token: str
    session_id: str
    rotated_from: str
    assurance: AssuranceLevel
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RevokeSessionCommand:
    session_id: str


@dataclass(frozen=True, slots=True)
class RevokeSessionResult:
    session_id: str
    revoked: bool


@dataclass(frozen=True, slots=True)
class DescribeSessionQuery:
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    subject_id: str
    assurance: AssuranceLevel
    created_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    tenant_scope: str | None


@dataclass(frozen=True, slots=True)
class EnrollMfaCommand:
    subject_id: str
    factor_type: MfaFactorType
    material: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnrollMfaResult:
    credential_id: str
    factor_type: MfaFactorType
    is_phishing_resistant: bool
    is_high_assurance: bool


@dataclass(frozen=True, slots=True)
class VerifyMfaCommand:
    subject_id: str
    factor_type: MfaFactorType
    proof: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifyMfaResult:
    subject_id: str
    factor_type: MfaFactorType
    verified: bool


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    """Extract the command payload / query parameters and narrow it to ``expected``.

    Raises :class:`TypeError` if the invocation does not carry the expected typed payload — a
    programming error at the wiring boundary, never a user-facing condition.
    """
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class RegisterSubject:
    """``identity.subject.register`` — create a new security principal (FR-IDN-001)."""

    def __init__(
        self, *, directory: IdentityDirectoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._directory = directory
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RegisterSubjectResult:
        command = _typed(request, RegisterSubjectCommand)
        subject = Subject(
            subject_id=self._id_factory(),
            subject_type=command.subject_type,
            created_at=self._clock(),
            tenant_scope=command.tenant_scope,
        )
        self._directory.add_subject(subject)
        return RegisterSubjectResult(
            subject_id=subject.subject_id, subject_type=subject.subject_type
        )


class BeginAuthentication:
    """``identity.authentication.begin`` — start Authorization Code + PKCE (FR-IDN-002)."""

    def __init__(
        self,
        *,
        provider: OidcProviderPort,
        transactions: AuthTransactionStorePort,
        clock: Clock,
    ) -> None:
        self._provider = provider
        self._transactions = transactions
        self._clock = clock

    def handle(self, request: object) -> BeginAuthenticationResult:
        command = _typed(request, BeginAuthenticationCommand)
        pkce = create_pkce_challenge()
        state = generate_state()
        nonce = generate_nonce()
        url = self._provider.build_authorization_url(
            redirect_uri=command.redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=pkce.code_challenge,
            code_challenge_method=pkce.code_challenge_method,
            scopes=command.scopes,
        )
        self._transactions.save(
            AuthTransaction(
                state=state,
                nonce=nonce,
                code_verifier=pkce.code_verifier,
                redirect_uri=command.redirect_uri,
                created_at=self._clock(),
            )
        )
        return BeginAuthenticationResult(authorization_url=url, state=state)


class CompleteAuthentication:
    """``identity.authentication.complete`` — validate the callback and mint a session.

    Validates ``state`` (against the cookie-bound value and the stored transaction), exchanges the
    code with PKCE, then validates issuer/audience/nonce before provisioning and issuing a session.
    Every failure path raises the *same* :class:`AuthenticationFailed` (anti-enumeration).
    """

    def __init__(
        self,
        *,
        provider: OidcProviderPort,
        transactions: AuthTransactionStorePort,
        directory: IdentityDirectoryPort,
        sessions: SessionStorePort,
        clock: Clock,
        id_factory: IdFactory,
        token_factory: TokenFactory,
        policy: SessionPolicy = _DEFAULT_SESSION_POLICY,
        tenant_scope: str | None = None,
    ) -> None:
        self._provider = provider
        self._transactions = transactions
        self._directory = directory
        self._sessions = sessions
        self._clock = clock
        self._id_factory = id_factory
        self._token_factory = token_factory
        self._policy = policy
        self._tenant_scope = tenant_scope

    def handle(self, request: object) -> CompleteAuthenticationResult:
        command = _typed(request, CompleteAuthenticationCommand)

        # State must be present and match the HttpOnly cookie-bound value (RFC 9700 CSRF defense).
        if not command.state or command.state != command.state_cookie:
            raise AuthenticationFailed("state_mismatch")

        transaction = self._transactions.pop(command.state)
        if transaction is None:
            raise AuthenticationFailed("unknown_or_replayed_state")

        try:
            claims = self._provider.exchange_code(
                code=command.code,
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
            )
        except OidcProviderError as exc:
            raise AuthenticationFailed("code_exchange_failed") from exc

        if claims.issuer != self._provider.issuer:
            raise AuthenticationFailed("issuer_mismatch")
        if claims.audience != self._provider.audience:
            raise AuthenticationFailed("audience_mismatch")
        if claims.nonce is None or claims.nonce != transaction.nonce:
            raise AuthenticationFailed("nonce_mismatch")

        identity = ExternalIdentity(issuer=claims.issuer, subject=claims.subject)
        existing = self._directory.find_by_external_identity(identity)
        provisioned = existing is None
        if existing is None:
            subject, _user = self._directory.provision(
                identity=identity,
                email=claims.email,
                display_name=None,
                tenant_scope=self._tenant_scope,
            )
        else:
            subject, _user = existing

        now = self._clock()
        raw_token = self._token_factory()
        session = Session(
            session_id=self._id_factory(),
            subject_id=subject.subject_id,
            created_at=now,
            idle_expires_at=now + self._policy.idle_ttl,
            absolute_expires_at=now + self._policy.absolute_ttl,
            assurance=AssuranceLevel.SINGLE_FACTOR,
            tenant_scope=subject.tenant_scope,
        )
        self._sessions.create(raw_token=raw_token, session=session)
        return CompleteAuthenticationResult(
            raw_session_token=raw_token,
            session_id=session.session_id,
            subject_id=subject.subject_id,
            assurance=session.assurance,
            absolute_expires_at=session.absolute_expires_at,
            provisioned=provisioned,
        )


class RotateSession:
    """``identity.session.rotate`` — issue a fresh session id, revoking the old (FR-IDN-003).

    Rotation is mandatory on a privilege change (docs/07 §3): the absolute deadline is preserved
    so rotation cannot silently extend the session's total lifetime.
    """

    def __init__(
        self,
        *,
        sessions: SessionStorePort,
        clock: Clock,
        id_factory: IdFactory,
        token_factory: TokenFactory,
        policy: SessionPolicy = _DEFAULT_SESSION_POLICY,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._id_factory = id_factory
        self._token_factory = token_factory
        self._policy = policy

    def handle(self, request: object) -> RotateSessionResult:
        command = _typed(request, RotateSessionCommand)
        now = self._clock()
        current = self._sessions.get(command.session_id)
        if current is None or not current.is_active(now):
            raise SessionNotAuthenticated()

        assurance = command.raise_to or current.assurance
        raw_token = self._token_factory()
        rotated = Session(
            session_id=self._id_factory(),
            subject_id=current.subject_id,
            created_at=now,
            idle_expires_at=min(now + self._policy.idle_ttl, current.absolute_expires_at),
            absolute_expires_at=current.absolute_expires_at,
            assurance=assurance,
            tenant_scope=current.tenant_scope,
            rotated_from=current.session_id,
        )
        self._sessions.create(raw_token=raw_token, session=rotated)
        self._sessions.replace(current.revoked(now))
        return RotateSessionResult(
            raw_session_token=raw_token,
            session_id=rotated.session_id,
            rotated_from=current.session_id,
            assurance=rotated.assurance,
            absolute_expires_at=rotated.absolute_expires_at,
        )


class RevokeSession:
    """``identity.session.revoke`` — explicit revocation / logout (FR-IDN-003).

    The result is uniform whether or not the session existed, so revocation cannot be used to
    probe for valid session ids (anti-enumeration).
    """

    def __init__(self, *, sessions: SessionStorePort, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def handle(self, request: object) -> RevokeSessionResult:
        command = _typed(request, RevokeSessionCommand)
        current = self._sessions.get(command.session_id)
        if current is not None:
            self._sessions.replace(current.revoked(self._clock()))
        return RevokeSessionResult(session_id=command.session_id, revoked=True)


class DescribeSession:
    """``identity.session.describe`` (query) — read the active session (FR-IDN-003)."""

    def __init__(self, *, sessions: SessionStorePort, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def handle(self, request: object) -> SessionView:
        query = _typed(request, DescribeSessionQuery)
        current = self._sessions.get(query.session_id)
        if current is None or not current.is_active(self._clock()):
            raise SessionNotAuthenticated()
        return SessionView(
            session_id=current.session_id,
            subject_id=current.subject_id,
            assurance=current.assurance,
            created_at=current.created_at,
            idle_expires_at=current.idle_expires_at,
            absolute_expires_at=current.absolute_expires_at,
            tenant_scope=current.tenant_scope,
        )


class EnrollMfa:
    """``identity.mfa.enroll`` — register an authentication factor (FR-IDN-004)."""

    def __init__(self, *, enrollment: MfaEnrollmentPort) -> None:
        self._enrollment = enrollment

    def handle(self, request: object) -> EnrollMfaResult:
        command = _typed(request, EnrollMfaCommand)
        credential = self._enrollment.enroll(
            subject_id=command.subject_id,
            factor_type=command.factor_type,
            material=command.material,
        )
        return EnrollMfaResult(
            credential_id=credential.credential_id,
            factor_type=credential.factor_type,
            is_phishing_resistant=credential.is_phishing_resistant,
            is_high_assurance=credential.is_high_assurance,
        )


class VerifyMfa:
    """``identity.mfa.verify`` — verify a presented authentication factor (FR-IDN-004)."""

    def __init__(self, *, verification: MfaVerificationPort) -> None:
        self._verification = verification

    def handle(self, request: object) -> VerifyMfaResult:
        command = _typed(request, VerifyMfaCommand)
        ok = self._verification.verify(
            subject_id=command.subject_id,
            factor_type=command.factor_type,
            proof=command.proof,
        )
        if not ok:
            raise MfaVerificationFailed()
        return VerifyMfaResult(
            subject_id=command.subject_id,
            factor_type=command.factor_type,
            verified=True,
        )
