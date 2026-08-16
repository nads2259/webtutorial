"""SCIM/provisioning gateway that REUSES the identity capability (FR-IDN-006, no core fork).

The :class:`IdentitySubjectGateway` implements :class:`ScimProvisioningPort` by delegating to the
identity module's own :class:`IdentityDirectoryPort` (resolve-or-provision a subject/user keyed by
the external identity ``(issuer, subject)``) and to identity's own session invalidation for
deprovisioning. Enterprise never forks identity's subject/session model and never writes identity's
tables directly: the subject write is delegated to the identity directory, and the session-revoke
write is delegated to identity's :class:`SessionStorePort` via the identity :class:`Session` domain
object's own ``revoked`` transition.

``InMemorySessionInvalidator`` is the reference session-invalidation adapter: it reuses an identity
:class:`SessionStorePort` and the identity session domain to revoke a subject's live sessions. A
real deployment injects an invalidator backed by identity's session index behind the same port.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from northstar.modules.identity.application.ports import (
    IdentityDirectoryPort,
    SessionStorePort,
)
from northstar.modules.identity.domain.model import ExternalIdentity

from ..application.ports import ProvisionedSubject

Clock = Callable[[], datetime]


@runtime_checkable
class SessionInvalidatorPort(Protocol):
    """Disables a subject's access by invalidating its live sessions (reuses identity)."""

    def invalidate_for_subject(self, subject_id: str) -> int: ...


class InMemorySessionInvalidator:
    """Reference session invalidator over an identity :class:`SessionStorePort`.

    It tracks a subject's session ids (registered as they are minted) and, on invalidation, revokes
    each still-live session by delegating to identity's own store + session domain transition — the
    revoke WRITE happens through identity, so identity stays authoritative over sessions (no fork,
    no cross-module table write).
    """

    def __init__(self, *, sessions: SessionStorePort, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock
        self._by_subject: dict[str, set[str]] = {}

    def record(self, *, subject_id: str, session_id: str) -> None:
        """Register a minted session so a later deprovision can invalidate it (reference seam)."""
        self._by_subject.setdefault(subject_id, set()).add(session_id)

    def invalidate_for_subject(self, subject_id: str) -> int:
        now = self._clock()
        invalidated = 0
        for session_id in self._by_subject.get(subject_id, set()):
            session = self._sessions.get(session_id)
            if session is None or session.revoked_at is not None:
                continue
            self._sessions.replace(session.revoked(now))
            invalidated += 1
        return invalidated


class _NullSessionInvalidator:
    """Fallback invalidator (no session backend wired): reports zero invalidations."""

    def invalidate_for_subject(self, subject_id: str) -> int:
        return 0


class IdentitySubjectGateway:
    """Implements :class:`ScimProvisioningPort` by reusing the identity directory + sessions."""

    def __init__(
        self,
        *,
        directory: IdentityDirectoryPort,
        invalidator: SessionInvalidatorPort | None = None,
    ) -> None:
        self._directory = directory
        self._invalidator = invalidator or _NullSessionInvalidator()

    def resolve_or_provision(
        self,
        *,
        issuer: str,
        external_subject: str,
        email: str | None,
        display_name: str | None,
        tenant_scope: str | None,
    ) -> ProvisionedSubject:
        identity = ExternalIdentity(issuer=issuer, subject=external_subject)
        existing = self._directory.find_by_external_identity(identity)
        if existing is not None:
            subject, user = existing
            return ProvisionedSubject(
                subject_id=subject.subject_id, user_id=user.user_id, created=False
            )
        subject, user = self._directory.provision(
            identity=identity,
            email=email,
            display_name=display_name,
            tenant_scope=tenant_scope,
        )
        return ProvisionedSubject(subject_id=subject.subject_id, user_id=user.user_id, created=True)

    def disable_subject(self, subject_id: str) -> int:
        return self._invalidator.invalidate_for_subject(subject_id)
