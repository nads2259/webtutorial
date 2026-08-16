"""In-memory adapters for fast, deterministic unit tests (no database, no infra).

These implement the identity application ports with plain dictionaries so the capabilities can be
exercised without PostgreSQL. They enforce the same security-relevant behavior as the SQLAlchemy
adapters: the session store persists only the SHA-256 of the opaque token, and single-use
authorization transactions are popped on consumption.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from ..application.ports import (
    AuthTransaction,
    ScimUserResource,
)
from ..domain.mfa import TotpCredential, WebAuthnCredential
from ..domain.model import (
    Credential,
    ExternalIdentity,
    MfaFactorType,
    Session,
    Subject,
    SubjectType,
    User,
)
from .security import hash_session_token

IdFactory = Callable[[], str]


def _uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InMemoryAuthTransactionStore:
    """Single-use pending-authorization store keyed by ``state``."""

    def __init__(self) -> None:
        self._by_state: dict[str, AuthTransaction] = {}

    def save(self, transaction: AuthTransaction) -> None:
        self._by_state[transaction.state] = transaction

    def pop(self, state: str) -> AuthTransaction | None:
        return self._by_state.pop(state, None)


class InMemoryIdentityDirectory:
    """In-memory subjects/users keyed by external identity ``(issuer, subject)``."""

    def __init__(
        self, *, id_factory: IdFactory = _uuid, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._subjects: dict[str, Subject] = {}
        self._users: dict[str, User] = {}
        self._by_identity: dict[tuple[str, str], str] = {}  # (issuer, subject) -> user_id

    def add_subject(self, subject: Subject) -> None:
        self._subjects[subject.subject_id] = subject

    def find_by_external_identity(self, identity: ExternalIdentity) -> tuple[Subject, User] | None:
        user_id = self._by_identity.get((identity.issuer, identity.subject))
        if user_id is None:
            return None
        user = self._users[user_id]
        return self._subjects[user.subject_id], user

    def provision(
        self,
        *,
        identity: ExternalIdentity,
        email: str | None,
        display_name: str | None,
        tenant_scope: str | None,
    ) -> tuple[Subject, User]:
        subject = Subject(
            subject_id=self._id_factory(),
            subject_type=SubjectType.USER,
            created_at=self._clock(),
            tenant_scope=tenant_scope,
        )
        user = User(
            user_id=self._id_factory(),
            subject_id=subject.subject_id,
            external_identities=(identity,),
            primary_email=email,
            display_name=display_name,
        )
        self._subjects[subject.subject_id] = subject
        self._users[user.user_id] = user
        self._by_identity[(identity.issuer, identity.subject)] = user.user_id
        return subject, user


class InMemorySessionStore:
    """In-memory session store; persists only the token hash (docs/07 §4)."""

    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}
        self._id_by_hash: dict[str, str] = {}

    def create(self, *, raw_token: str, session: Session) -> None:
        self._by_id[session.session_id] = session
        self._id_by_hash[hash_session_token(raw_token)] = session.session_id

    def authenticate(self, *, raw_token: str, now: datetime) -> Session | None:
        session_id = self._id_by_hash.get(hash_session_token(raw_token))
        if session_id is None:
            return None
        session = self._by_id.get(session_id)
        if session is None or not session.is_active(now):
            return None
        return session

    def get(self, session_id: str) -> Session | None:
        return self._by_id.get(session_id)

    def replace(self, session: Session) -> None:
        self._by_id[session.session_id] = session


class InMemoryMfaService:
    """Reference MFA enrollment + verification (passkey-preferred).

    Verification succeeds when the subject has an enrolled credential of the requested factor and
    the proof is not explicitly marked invalid — enough to exercise the port contract and the
    passkey-vs-SMS assurance distinction without a full WebAuthn implementation (an adapter).
    """

    def __init__(
        self, *, id_factory: IdFactory = _uuid, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._credentials: dict[str, list[Credential]] = {}

    def enroll(
        self,
        *,
        subject_id: str,
        factor_type: MfaFactorType,
        material: Mapping[str, object],
    ) -> Credential:
        credential = Credential(
            credential_id=self._id_factory(),
            subject_id=subject_id,
            factor_type=factor_type,
            created_at=self._clock(),
            label=str(material.get("label")) if material.get("label") is not None else None,
        )
        self._credentials.setdefault(subject_id, []).append(credential)
        return credential

    def verify(
        self,
        *,
        subject_id: str,
        factor_type: MfaFactorType,
        proof: Mapping[str, object],
    ) -> bool:
        if proof.get("valid") is False:
            return False
        return any(c.factor_type == factor_type for c in self._credentials.get(subject_id, []))


class InMemoryTotpCredentialStore:
    """In-memory TOTP store (one credential per subject) for fast unit/integration tests."""

    def __init__(self) -> None:
        self._by_subject: dict[str, TotpCredential] = {}

    def save(self, credential: TotpCredential) -> None:
        self._by_subject[credential.subject_id] = credential

    def get(self, subject_id: str) -> TotpCredential | None:
        return self._by_subject.get(subject_id)

    def replace(self, credential: TotpCredential) -> None:
        self._by_subject[credential.subject_id] = credential

    def delete_for_subject(self, subject_id: str) -> None:
        self._by_subject.pop(subject_id, None)


class InMemoryWebAuthnCredentialStore:
    """In-memory WebAuthn credential store keyed by Base64URL credential id."""

    def __init__(self) -> None:
        self._by_id: dict[str, WebAuthnCredential] = {}

    def save(self, credential: WebAuthnCredential) -> None:
        self._by_id[credential.credential_id] = credential

    def get(self, *, credential_id: str) -> WebAuthnCredential | None:
        return self._by_id.get(credential_id)

    def list_for_subject(self, subject_id: str) -> tuple[WebAuthnCredential, ...]:
        return tuple(c for c in self._by_id.values() if c.subject_id == subject_id)

    def set_sign_count(self, *, credential_id: str, sign_count: int) -> None:
        current = self._by_id.get(credential_id)
        if current is not None:
            self._by_id[credential_id] = current.with_sign_count(sign_count)

    def delete_for_subject(self, subject_id: str) -> None:
        for cid in [c.credential_id for c in self._by_id.values() if c.subject_id == subject_id]:
            self._by_id.pop(cid, None)


class InMemoryWebAuthnChallengeStore:
    """Single-use WebAuthn challenge store keyed by ``(subject_id, ceremony)``."""

    def __init__(self) -> None:
        self._challenges: dict[tuple[str, str], bytes] = {}

    def save(self, *, subject_id: str, ceremony: str, challenge: bytes) -> None:
        self._challenges[(subject_id, ceremony)] = challenge

    def pop(self, *, subject_id: str, ceremony: str) -> bytes | None:
        return self._challenges.pop((subject_id, ceremony), None)


class InMemoryScimProvisioner:
    """Reference SCIM provisioning adapter (FR-IDN-006) over an in-memory directory.

    Demonstrates federation/SCIM as an adapter port, not an identity-core fork. JIT provisioning
    never auto-grants privileged roles (docs/07 §3).
    """

    _SCIM_ISSUER = "scim://enterprise"

    def __init__(self, directory: InMemoryIdentityDirectory) -> None:
        self._directory = directory

    def provision(self, resource: ScimUserResource) -> tuple[Subject, User]:
        identity = ExternalIdentity(issuer=self._SCIM_ISSUER, subject=resource.external_id)
        existing = self._directory.find_by_external_identity(identity)
        if existing is not None:
            return existing
        return self._directory.provision(
            identity=identity,
            email=resource.email,
            display_name=resource.display_name,
            tenant_scope=None,
        )

    def deprovision(self, external_id: str) -> None:
        # Reference no-op: a real adapter would revoke sessions and mark the account inactive.
        _ = external_id


def new_session_token() -> str:
    """Return a fresh opaque session token (URL-safe, high entropy)."""
    return secrets.token_urlsafe(32)
