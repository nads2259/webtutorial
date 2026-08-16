"""Real MFA capabilities: TOTP + WebAuthn/passkey enrollment, verification and step-up (LAW-04).

These are the one authoritative implementations (FR-IDN-003) invoked through the kernel
command/query buses, so every second-factor action is authorized deny-by-default and recorded as
tamper-evident audit evidence (rule 50, LAW-14). The handlers depend only on the ports in
:mod:`.ports` and the pure :mod:`..domain` (TOTP algorithm, MFA records, session step-up);
py_webauthn lives behind :class:`WebAuthnVerifierPort` in the adapters layer.

A session becomes *step-up satisfied* only after a successful second factor: TOTP raises it to the
multi-factor tier, a passkey to the phishing-resistant tier. Privileged actions call
:class:`EnforceStepUp` (``identity.mfa.step-up.enforce``) which denies until that has happened.
SMS is deliberately not offered as a factor here (docs/07 §3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import (
    MfaVerificationFailed,
    SessionNotAuthenticated,
    StepUpRequired,
)
from ..domain.mfa import TotpCredential, WebAuthnCredential
from ..domain.model import AssuranceLevel, Session
from ..domain.totp import (
    DEFAULT_ALGORITHM,
    DEFAULT_DIGITS,
    DEFAULT_PERIOD,
    generate_totp_secret,
    provisioning_uri,
    verify_totp,
)
from .capabilities import Clock, IdFactory, _typed
from .ports import (
    SessionStorePort,
    TotpCredentialStorePort,
    WebAuthnChallengeStorePort,
    WebAuthnCredentialStorePort,
    WebAuthnVerifierPort,
)

CAP_ENROLL_TOTP = "identity.mfa.totp.enroll"
CAP_VERIFY_TOTP = "identity.mfa.totp.verify"
CAP_BEGIN_WEBAUTHN_REGISTRATION = "identity.mfa.webauthn.register.begin"
CAP_COMPLETE_WEBAUTHN_REGISTRATION = "identity.mfa.webauthn.register.complete"
CAP_BEGIN_WEBAUTHN_AUTHENTICATION = "identity.mfa.webauthn.authenticate.begin"
CAP_COMPLETE_WEBAUTHN_AUTHENTICATION = "identity.mfa.webauthn.authenticate.complete"
CAP_RESET_MFA = "identity.mfa.reset"
CAP_ENFORCE_STEP_UP = "identity.mfa.step-up.enforce"

_REGISTRATION_CEREMONY = "webauthn.create"
_AUTHENTICATION_CEREMONY = "webauthn.get"


@dataclass(frozen=True, slots=True)
class TotpPolicy:
    """Issuer + parameters stamped into every enrolled TOTP secret (docs/07 §3 defaults)."""

    issuer: str = "Northstar"
    digits: int = DEFAULT_DIGITS
    period: int = DEFAULT_PERIOD
    algorithm: str = DEFAULT_ALGORITHM
    window: int = 1


_DEFAULT_TOTP_POLICY = TotpPolicy()


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnrollTotpCommand:
    subject_id: str
    account_name: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class EnrollTotpResult:
    credential_id: str
    secret: str
    provisioning_uri: str
    digits: int
    period: int
    algorithm: str


@dataclass(frozen=True, slots=True)
class VerifyTotpCommand:
    subject_id: str
    code: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyTotpResult:
    subject_id: str
    verified: bool
    assurance: AssuranceLevel | None
    mfa_satisfied: bool


@dataclass(frozen=True, slots=True)
class BeginWebAuthnRegistrationCommand:
    subject_id: str
    user_name: str


@dataclass(frozen=True, slots=True)
class BeginWebAuthnRegistrationResult:
    options_json: str


@dataclass(frozen=True, slots=True)
class CompleteWebAuthnRegistrationCommand:
    subject_id: str
    response: Mapping[str, object]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteWebAuthnRegistrationResult:
    credential_id: str
    sign_count: int
    is_phishing_resistant: bool = True


@dataclass(frozen=True, slots=True)
class BeginWebAuthnAuthenticationCommand:
    subject_id: str


@dataclass(frozen=True, slots=True)
class BeginWebAuthnAuthenticationResult:
    options_json: str


@dataclass(frozen=True, slots=True)
class CompleteWebAuthnAuthenticationCommand:
    subject_id: str
    response: Mapping[str, object]
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteWebAuthnAuthenticationResult:
    subject_id: str
    credential_id: str
    verified: bool
    new_sign_count: int
    assurance: AssuranceLevel | None


@dataclass(frozen=True, slots=True)
class ResetMfaCommand:
    subject_id: str


@dataclass(frozen=True, slots=True)
class ResetMfaResult:
    subject_id: str
    reset: bool


@dataclass(frozen=True, slots=True)
class EnforceStepUpQuery:
    session_id: str
    required: AssuranceLevel = AssuranceLevel.MULTI_FACTOR


@dataclass(frozen=True, slots=True)
class StepUpView:
    session_id: str
    subject_id: str
    assurance: AssuranceLevel
    satisfied: bool


def _step_up_session(
    sessions: SessionStorePort, *, session_id: str, level: AssuranceLevel, now: datetime
) -> AssuranceLevel:
    """Raise the assurance of an active session to ``level`` (step-up); return the new level.

    Raises :class:`SessionNotAuthenticated` when the session is missing, expired or revoked so a
    second factor can never elevate a session that is not currently valid.
    """
    current = sessions.get(session_id)
    if current is None or not current.is_active(now):
        raise SessionNotAuthenticated()
    stepped: Session = current.stepped_up(level)
    sessions.replace(stepped)
    return stepped.assurance


# ---------------------------------------------------------------------------
# TOTP capability handlers
# ---------------------------------------------------------------------------


class EnrollTotp:
    """``identity.mfa.totp.enroll`` — generate a TOTP secret + provisioning URI (FR-IDN-003)."""

    def __init__(
        self,
        *,
        totp_store: TotpCredentialStorePort,
        clock: Clock,
        id_factory: IdFactory,
        policy: TotpPolicy = _DEFAULT_TOTP_POLICY,
    ) -> None:
        self._totp_store = totp_store
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy

    def handle(self, request: object) -> EnrollTotpResult:
        command = _typed(request, EnrollTotpCommand)
        secret = generate_totp_secret()
        credential = TotpCredential(
            credential_id=self._id_factory(),
            subject_id=command.subject_id,
            secret=secret,
            digits=self._policy.digits,
            period=self._policy.period,
            algorithm=self._policy.algorithm,
            created_at=self._clock(),
            confirmed_at=None,
            last_used_step=None,
            label=command.label,
        )
        self._totp_store.save(credential)
        uri = provisioning_uri(
            secret,
            account_name=command.account_name,
            issuer=self._policy.issuer,
            digits=self._policy.digits,
            period=self._policy.period,
            algorithm=self._policy.algorithm,
        )
        return EnrollTotpResult(
            credential_id=credential.credential_id,
            secret=secret,
            provisioning_uri=uri,
            digits=self._policy.digits,
            period=self._policy.period,
            algorithm=self._policy.algorithm,
        )


class VerifyTotp:
    """``identity.mfa.totp.verify`` — verify a TOTP code with replay protection + step-up.

    A code is accepted only within a small ±window and only for a step strictly beyond the
    persisted ``last_used_step``; the matched step is then persisted so the same (or an earlier)
    code cannot be replayed. On success an active ``session_id`` is stepped up to the multi-factor
    tier (``mfa_satisfied``).
    """

    def __init__(
        self,
        *,
        totp_store: TotpCredentialStorePort,
        sessions: SessionStorePort,
        clock: Clock,
        policy: TotpPolicy = _DEFAULT_TOTP_POLICY,
    ) -> None:
        self._totp_store = totp_store
        self._sessions = sessions
        self._clock = clock
        self._policy = policy

    def handle(self, request: object) -> VerifyTotpResult:
        command = _typed(request, VerifyTotpCommand)
        credential = self._totp_store.get(command.subject_id)
        if credential is None:
            raise MfaVerificationFailed("totp.not_enrolled")
        now = self._clock()
        matched_step = verify_totp(
            credential.secret,
            command.code,
            timestamp=int(now.timestamp()),
            period=credential.period,
            digits=credential.digits,
            algorithm=credential.algorithm,
            window=self._policy.window,
            last_used_step=credential.last_used_step,
        )
        if matched_step is None:
            raise MfaVerificationFailed("totp.invalid_or_replayed")
        self._totp_store.replace(credential.confirmed(step=matched_step, now=now))

        assurance: AssuranceLevel | None = None
        if command.session_id is not None:
            assurance = _step_up_session(
                self._sessions,
                session_id=command.session_id,
                level=AssuranceLevel.MULTI_FACTOR,
                now=now,
            )
        return VerifyTotpResult(
            subject_id=command.subject_id,
            verified=True,
            assurance=assurance,
            mfa_satisfied=assurance
            in {AssuranceLevel.MULTI_FACTOR, AssuranceLevel.PHISHING_RESISTANT}
            if assurance is not None
            else False,
        )


# ---------------------------------------------------------------------------
# WebAuthn / passkey capability handlers
# ---------------------------------------------------------------------------


class BeginWebAuthnRegistration:
    """``identity.mfa.webauthn.register.begin`` — issue registration options + challenge."""

    def __init__(
        self,
        *,
        verifier: WebAuthnVerifierPort,
        challenges: WebAuthnChallengeStorePort,
        webauthn_store: WebAuthnCredentialStorePort,
    ) -> None:
        self._verifier = verifier
        self._challenges = challenges
        self._webauthn_store = webauthn_store

    def handle(self, request: object) -> BeginWebAuthnRegistrationResult:
        command = _typed(request, BeginWebAuthnRegistrationCommand)
        existing = tuple(
            c.credential_id for c in self._webauthn_store.list_for_subject(command.subject_id)
        )
        options_json, challenge = self._verifier.build_registration_options(
            subject_id=command.subject_id,
            user_name=command.user_name,
            existing_credential_ids=existing,
        )
        self._challenges.save(
            subject_id=command.subject_id, ceremony=_REGISTRATION_CEREMONY, challenge=challenge
        )
        return BeginWebAuthnRegistrationResult(options_json=options_json)


class CompleteWebAuthnRegistration:
    """``identity.mfa.webauthn.register.complete`` — verify attestation, store the passkey."""

    def __init__(
        self,
        *,
        verifier: WebAuthnVerifierPort,
        challenges: WebAuthnChallengeStorePort,
        webauthn_store: WebAuthnCredentialStorePort,
        clock: Clock,
    ) -> None:
        self._verifier = verifier
        self._challenges = challenges
        self._webauthn_store = webauthn_store
        self._clock = clock

    def handle(self, request: object) -> CompleteWebAuthnRegistrationResult:
        command = _typed(request, CompleteWebAuthnRegistrationCommand)
        challenge = self._challenges.pop(
            subject_id=command.subject_id, ceremony=_REGISTRATION_CEREMONY
        )
        if challenge is None:
            raise MfaVerificationFailed("webauthn.no_registration_challenge")
        verified = self._verifier.verify_registration(
            response=command.response, expected_challenge=challenge
        )
        credential = WebAuthnCredential(
            credential_id=verified.credential_id,
            subject_id=command.subject_id,
            public_key=verified.public_key,
            sign_count=verified.sign_count,
            rp_id=self._verifier.rp_id,
            origin=self._verifier.origin,
            created_at=self._clock(),
            aaguid=verified.aaguid,
            label=command.label,
        )
        self._webauthn_store.save(credential)
        return CompleteWebAuthnRegistrationResult(
            credential_id=credential.credential_id, sign_count=credential.sign_count
        )


class BeginWebAuthnAuthentication:
    """``identity.mfa.webauthn.authenticate.begin`` — issue assertion options + challenge."""

    def __init__(
        self,
        *,
        verifier: WebAuthnVerifierPort,
        challenges: WebAuthnChallengeStorePort,
        webauthn_store: WebAuthnCredentialStorePort,
    ) -> None:
        self._verifier = verifier
        self._challenges = challenges
        self._webauthn_store = webauthn_store

    def handle(self, request: object) -> BeginWebAuthnAuthenticationResult:
        command = _typed(request, BeginWebAuthnAuthenticationCommand)
        allow = tuple(
            c.credential_id for c in self._webauthn_store.list_for_subject(command.subject_id)
        )
        options_json, challenge = self._verifier.build_authentication_options(
            allow_credential_ids=allow
        )
        self._challenges.save(
            subject_id=command.subject_id, ceremony=_AUTHENTICATION_CEREMONY, challenge=challenge
        )
        return BeginWebAuthnAuthenticationResult(options_json=options_json)


class CompleteWebAuthnAuthentication:
    """``identity.mfa.webauthn.authenticate.complete`` — verify assertion + step-up.

    Rejects a signature-count regression (a cloned authenticator) via the verifier, advances the
    stored counter, and raises an active ``session_id`` to the phishing-resistant tier.
    """

    def __init__(
        self,
        *,
        verifier: WebAuthnVerifierPort,
        challenges: WebAuthnChallengeStorePort,
        webauthn_store: WebAuthnCredentialStorePort,
        sessions: SessionStorePort,
        clock: Clock,
    ) -> None:
        self._verifier = verifier
        self._challenges = challenges
        self._webauthn_store = webauthn_store
        self._sessions = sessions
        self._clock = clock

    def handle(self, request: object) -> CompleteWebAuthnAuthenticationResult:
        command = _typed(request, CompleteWebAuthnAuthenticationCommand)
        challenge = self._challenges.pop(
            subject_id=command.subject_id, ceremony=_AUTHENTICATION_CEREMONY
        )
        if challenge is None:
            raise MfaVerificationFailed("webauthn.no_authentication_challenge")
        credential_id = str(command.response.get("id") or "")
        credential = self._webauthn_store.get(credential_id=credential_id)
        if credential is None or credential.subject_id != command.subject_id:
            raise MfaVerificationFailed("webauthn.unknown_credential")
        new_sign_count = self._verifier.verify_authentication(
            response=command.response,
            expected_challenge=challenge,
            credential_public_key=credential.public_key,
            current_sign_count=credential.sign_count,
        )
        self._webauthn_store.set_sign_count(
            credential_id=credential.credential_id, sign_count=new_sign_count
        )
        now = self._clock()
        assurance: AssuranceLevel | None = None
        if command.session_id is not None:
            assurance = _step_up_session(
                self._sessions,
                session_id=command.session_id,
                level=AssuranceLevel.PHISHING_RESISTANT,
                now=now,
            )
        return CompleteWebAuthnAuthenticationResult(
            subject_id=command.subject_id,
            credential_id=credential.credential_id,
            verified=True,
            new_sign_count=new_sign_count,
            assurance=assurance,
        )


class ResetMfa:
    """``identity.mfa.reset`` — remove a subject's TOTP + WebAuthn factors (recovery workflow)."""

    def __init__(
        self,
        *,
        totp_store: TotpCredentialStorePort,
        webauthn_store: WebAuthnCredentialStorePort,
    ) -> None:
        self._totp_store = totp_store
        self._webauthn_store = webauthn_store

    def handle(self, request: object) -> ResetMfaResult:
        command = _typed(request, ResetMfaCommand)
        self._totp_store.delete_for_subject(command.subject_id)
        self._webauthn_store.delete_for_subject(command.subject_id)
        return ResetMfaResult(subject_id=command.subject_id, reset=True)


class EnforceStepUp:
    """``identity.mfa.step-up.enforce`` (query) — the authoritative step-up guard (FR-IDN-003).

    Privileged capabilities/routes call this before acting: it raises
    :class:`SessionNotAuthenticated` for an invalid session and :class:`StepUpRequired` when the
    session has not satisfied a recent second factor, so a privileged action is denied until MFA.
    """

    def __init__(self, *, sessions: SessionStorePort, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def handle(self, request: object) -> StepUpView:
        query = _typed(request, EnforceStepUpQuery)
        session = self._sessions.get(query.session_id)
        if session is None or not session.is_active(self._clock()):
            raise SessionNotAuthenticated()
        if not session.mfa_satisfied:
            raise StepUpRequired()
        return StepUpView(
            session_id=session.session_id,
            subject_id=session.subject_id,
            assurance=session.assurance,
            satisfied=True,
        )
