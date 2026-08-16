"""Identity domain value objects and invariants (pure, docs/07 §2-4).

Frozen dataclasses model the security principal (:class:`Subject`), the account that links one or
more external identities (:class:`User`), the server-managed :class:`Session` and an
authentication :class:`Credential`. Invariants live here (LAW-06 spirit / rule 20): a session's
absolute and idle deadlines are validated on construction, and state transitions (idle refresh,
rotation, revocation) return new immutable instances rather than mutating in place.

Design notes drawn from the spec:

* Email is **not** a stable identity key (docs/07 §2); external identities are keyed by
  ``(issuer, subject)``.
* Passkeys/WebAuthn are the preferred phishing-resistant factor; SMS is **not** a default
  high-assurance factor (docs/07 §3).
* Sessions carry an assurance level so step-up (privilege change) can trigger rotation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from .errors import SessionInvariantViolation


class SubjectType(StrEnum):
    """Kinds of security principal Northstar supports (docs/07 §2)."""

    USER = "user"
    SERVICE = "service"
    EXTERNAL_CLIENT = "external_client"
    EXTENSION = "extension"
    AI_ACTOR = "ai_actor"
    OPERATOR = "operator"


class MfaFactorType(StrEnum):
    """Authentication factor kinds. Passkeys are preferred; SMS is low-assurance (docs/07 §3)."""

    PASSKEY = "passkey"  # WebAuthn — phishing-resistant, preferred
    TOTP = "totp"
    RECOVERY_CODE = "recovery_code"
    SMS = "sms"  # not a default high-assurance factor


class AssuranceLevel(StrEnum):
    """The strength of the authentication backing a session (ascending)."""

    UNAUTHENTICATED = "unauthenticated"
    SINGLE_FACTOR = "single_factor"
    MULTI_FACTOR = "multi_factor"
    PHISHING_RESISTANT = "phishing_resistant"


_ASSURANCE_ORDER: dict[AssuranceLevel, int] = {
    AssuranceLevel.UNAUTHENTICATED: 0,
    AssuranceLevel.SINGLE_FACTOR: 1,
    AssuranceLevel.MULTI_FACTOR: 2,
    AssuranceLevel.PHISHING_RESISTANT: 3,
}


PHISHING_RESISTANT_FACTORS: frozenset[MfaFactorType] = frozenset({MfaFactorType.PASSKEY})
LOW_ASSURANCE_FACTORS: frozenset[MfaFactorType] = frozenset({MfaFactorType.SMS})

# A session has satisfied step-up MFA once a second factor raised it to at least multi-factor
# (docs/07 §3): passkeys raise it to the phishing-resistant tier, TOTP to the multi-factor tier.
MFA_SATISFIED_LEVELS: frozenset[AssuranceLevel] = frozenset(
    {AssuranceLevel.MULTI_FACTOR, AssuranceLevel.PHISHING_RESISTANT}
)


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None:
        raise SessionInvariantViolation(f"{field} must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """A link to an external IdP identity, keyed by ``(issuer, subject)`` (docs/07 §2).

    Email is intentionally excluded as a key: it is neither stable nor unique across issuers.
    """

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        if not self.issuer:
            raise ValueError("external identity issuer must be non-empty")
        if not self.subject:
            raise ValueError("external identity subject must be non-empty")


@dataclass(frozen=True, slots=True)
class Subject:
    """A security principal (human, service, AI actor, operator, …)."""

    subject_id: str
    subject_type: SubjectType
    created_at: datetime
    tenant_scope: str | None = None

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("subject_id must be non-empty")
        _require_utc(self.created_at, "subject.created_at")


@dataclass(frozen=True, slots=True)
class User:
    """An account that may link multiple external identities (docs/07 §2)."""

    user_id: str
    subject_id: str
    external_identities: tuple[ExternalIdentity, ...] = ()
    primary_email: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user_id must be non-empty")
        if not self.subject_id:
            raise ValueError("user.subject_id must be non-empty")

    def has_identity(self, identity: ExternalIdentity) -> bool:
        return identity in self.external_identities

    def link_identity(self, identity: ExternalIdentity) -> User:
        """Return a copy with ``identity`` linked (idempotent; requires prior proof upstream)."""
        if self.has_identity(identity):
            return self
        return replace(self, external_identities=(*self.external_identities, identity))


@dataclass(frozen=True, slots=True)
class Credential:
    """A registered authentication factor for a subject (material stored by the adapter)."""

    credential_id: str
    subject_id: str
    factor_type: MfaFactorType
    created_at: datetime
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.credential_id:
            raise ValueError("credential_id must be non-empty")
        if not self.subject_id:
            raise ValueError("credential.subject_id must be non-empty")
        _require_utc(self.created_at, "credential.created_at")

    @property
    def is_phishing_resistant(self) -> bool:
        return self.factor_type in PHISHING_RESISTANT_FACTORS

    @property
    def is_high_assurance(self) -> bool:
        """Passkeys/TOTP/recovery are acceptable high-assurance factors; SMS is not (docs/07)."""
        return self.factor_type not in LOW_ASSURANCE_FACTORS


@dataclass(frozen=True, slots=True)
class Session:
    """A server-managed session addressed by an opaque, hashed session id (docs/07 §4).

    The raw session token never lives in the domain — only its identity and lifecycle. Idle and
    absolute deadlines bound the session; ``assurance`` records the strength of the backing
    authentication so a privilege change can force rotation to a fresh id.
    """

    session_id: str
    subject_id: str
    created_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    assurance: AssuranceLevel
    tenant_scope: str | None = None
    revoked_at: datetime | None = None
    rotated_from: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        if not self.subject_id:
            raise ValueError("session.subject_id must be non-empty")
        _require_utc(self.created_at, "session.created_at")
        _require_utc(self.idle_expires_at, "session.idle_expires_at")
        _require_utc(self.absolute_expires_at, "session.absolute_expires_at")
        if self.absolute_expires_at <= self.created_at:
            raise SessionInvariantViolation("session absolute_expires_at must be after created_at")
        if self.idle_expires_at > self.absolute_expires_at:
            raise SessionInvariantViolation(
                "session idle_expires_at must not exceed absolute_expires_at"
            )

    def is_active(self, now: datetime) -> bool:
        """True iff the session is neither revoked nor past its idle/absolute deadlines."""
        if self.revoked_at is not None:
            return False
        return now < self.idle_expires_at and now < self.absolute_expires_at

    @property
    def mfa_satisfied(self) -> bool:
        """True once a successful second factor raised the session to a multi-factor tier."""
        return self.assurance in MFA_SATISFIED_LEVELS

    def stepped_up(self, level: AssuranceLevel) -> Session:
        """Return a copy raised to ``level`` (a successful second factor; never lowers assurance).

        Step-up is monotonic: presenting a weaker factor after a stronger one does not downgrade
        an already phishing-resistant session.
        """
        if _ASSURANCE_ORDER[level] <= _ASSURANCE_ORDER[self.assurance]:
            return self
        return replace(self, assurance=level)

    def touched(self, now: datetime, idle_ttl: timedelta) -> Session:
        """Return a copy with the idle deadline extended, capped by the absolute deadline."""
        extended = min(now + idle_ttl, self.absolute_expires_at)
        return replace(self, idle_expires_at=extended)

    def revoked(self, now: datetime) -> Session:
        """Return a copy marked revoked at ``now`` (idempotent for an already-revoked session)."""
        if self.revoked_at is not None:
            return self
        _require_utc(now, "revoked_at")
        return replace(self, revoked_at=now)
