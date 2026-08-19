"""Ports for local (email + password) authentication (rule 10/20, DIP).

Each collaborator is a narrow Protocol so the local-auth capabilities never depend on a KDF library,
a database, or an email provider. Adapters in :mod:`..adapters` implement these; the composition root
injects concrete instances.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.local import AccountEvent, AccountEventType, PasswordCredential, VerificationToken


@runtime_checkable
class PasswordHasherPort(Protocol):
    """Hashes and verifies passwords with a salted, memory-hard KDF (never reversible)."""

    def hash(self, password: str) -> str: ...

    def verify(self, *, password: str, encoded: str) -> bool: ...


@runtime_checkable
class LocalAccountStorePort(Protocol):
    """Creates/reads local password accounts (subject + user + credential), tenant-scoped."""

    def find_by_email(self, *, organization_id: str, email: str) -> PasswordCredential | None: ...

    def create_account(
        self, *, organization_id: str, email: str, password_hash: str
    ) -> PasswordCredential:
        """Insert a new subject + user_account + password_credential (email unverified).

        Raises ``LocalAuthError`` if the email already has a local account (unique, case-insensitive).
        """
        ...

    def set_verified(self, *, organization_id: str, subject_id: str) -> None: ...

    def set_password(self, *, organization_id: str, subject_id: str, password_hash: str) -> None: ...


@runtime_checkable
class VerificationTokenStorePort(Protocol):
    """Persists single-use, expiring verification tokens by SHA-256 (never the raw token)."""

    def save(self, *, organization_id: str, token: VerificationToken) -> None: ...

    def find_by_hash(
        self, *, organization_id: str, token_sha256: str
    ) -> VerificationToken | None: ...

    def consume(self, *, organization_id: str, token_id: str, now: datetime) -> None: ...


@runtime_checkable
class AccountEventStorePort(Protocol):
    """Durable append-only account-activity log, tenant-scoped."""

    def record(self, *, event: AccountEvent) -> None: ...

    def list_for_subject(
        self, *, organization_id: str, subject_id: str, limit: int = 50
    ) -> Sequence[AccountEvent]: ...

    def list_for_tenant(
        self,
        *,
        organization_id: str,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        detail_query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Sequence[AccountEvent]: ...

    def count_for_tenant(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        detail_query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int: ...


@runtime_checkable
class TransactionalEmailPort(Protocol):
    """Sends a rendered transactional email (confirmation, password reset) durably.

    The adapter renders the named, admin-managed template with ``variables`` and records + delivers
    it; the local-auth capability supplies only the recipient, template id and variables.
    """

    def send(
        self,
        *,
        organization_id: str,
        template_id: str,
        to_email: str,
        variables: Mapping[str, str],
    ) -> None: ...


# Re-exported for adapter typing convenience.
__all__ = [
    "AccountEvent",
    "AccountEventStorePort",
    "AccountEventType",
    "LocalAccountStorePort",
    "PasswordCredential",
    "PasswordHasherPort",
    "TransactionalEmailPort",
    "VerificationToken",
    "VerificationTokenStorePort",
]
