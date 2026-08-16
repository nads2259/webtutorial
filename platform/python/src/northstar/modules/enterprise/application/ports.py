"""Ports (abstractions) for the enterprise application layer (rule 10/20, DIP, ISP).

Every collaborator the capabilities need is a small, role-specific Protocol so the application
depends only on abstractions — federation/SCIM/LTI/LRS providers are adapters behind these ports,
NOT identity-core forks (FR-IDN-006). Concrete adapters (a signature-verified reference federation
verifier, an identity-reusing SCIM/provisioning gateway, an LTI verifier, a reference LRS and the
SQLAlchemy repository) live in :mod:`..adapters` and are injected at the composition root.

The repository is tenant-aware: every read/write is scoped by ``organization_id`` so a caller can
never reach another tenant's federation mappings or provisioning records (rule 50).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.model import (
    FederatedIdentityMapping,
    FederationAssertion,
    LtiLaunch,
    ProvisioningRecord,
    VerifiedFederationClaims,
    XapiStatement,
)

# ---------------------------------------------------------------------------
# Federation verifier (signature-verified external IdP assertion)  (FR-IDN-006)
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationVerifierPort(Protocol):
    """Verifies a federated IdP assertion's signature + validity window (fail-closed).

    Returns :class:`VerifiedFederationClaims` for a genuinely signed, unexpired, correctly-audienced
    assertion, and ``None`` for ANY untrusted input (forged/tampered/unsigned/expired/unknown
    issuer/wrong audience). Implementations MUST NOT raise on a bad assertion — they fail closed by
    returning ``None`` (the capability turns that into a uniform rejection, EVAL-IDN-005).
    """

    def verify(
        self, assertion: FederationAssertion, *, now: datetime
    ) -> VerifiedFederationClaims | None: ...


# ---------------------------------------------------------------------------
# SCIM / provisioning gateway into the identity capability  (FR-IDN-006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvisionedSubject:
    """The Northstar subject a federated/SCIM identity resolves to (created or pre-existing)."""

    subject_id: str
    user_id: str
    created: bool


@runtime_checkable
class ScimProvisioningPort(Protocol):
    """Maps an external identity to a Northstar subject by REUSING the identity capability.

    This is the seam that keeps federation/SCIM an adapter: it resolves-or-provisions a subject
    through the identity directory (never forking identity's subject/session model) and disables a
    subject's access by reusing identity's session invalidation. JIT provisioning must not
    auto-grant privileged roles (docs/07 §3).
    """

    def resolve_or_provision(
        self,
        *,
        issuer: str,
        external_subject: str,
        email: str | None,
        display_name: str | None,
        tenant_scope: str | None,
    ) -> ProvisionedSubject: ...

    def disable_subject(self, subject_id: str) -> int:
        """Disable the subject's access; return the invalidated-session count (reuse identity)."""
        ...


# ---------------------------------------------------------------------------
# LTI launch verifier  (FR-LRN-008)
# ---------------------------------------------------------------------------


@runtime_checkable
class LtiVerifierPort(Protocol):
    """Verifies a signed LTI launch (fail-closed): ``True`` only for a genuine, unexpired launch."""

    def verify(self, launch: LtiLaunch, *, now: datetime) -> bool: ...


# ---------------------------------------------------------------------------
# Learning Record Store (xAPI export)  (FR-LRN-008)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LrsReceipt:
    """The acknowledgement an LRS returns when it stores an xAPI statement."""

    statement_id: str
    stored: bool


@runtime_checkable
class LrsPort(Protocol):
    """Emits an xAPI statement to a configured Learning Record Store (adapter behind a port)."""

    def emit(self, statement: XapiStatement) -> LrsReceipt: ...


# ---------------------------------------------------------------------------
# Export-consent directory (xAPI consent gate)  (FR-LRN-007 spirit)
# ---------------------------------------------------------------------------


@runtime_checkable
class ExportConsentPort(Protocol):
    """Deny-by-default consent gate: a learner must opt in before events leave the platform."""

    def has_export_consent(self, *, organization_id: str, subject_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Enterprise repository (federation mappings + provisioning records)  (rule 50 RLS)
# ---------------------------------------------------------------------------


@runtime_checkable
class EnterpriseRepositoryPort(Protocol):
    """Persists and reads federation mappings + provisioning records, tenant-scoped (FR-IDN-006)."""

    def get_mapping(
        self, *, organization_id: str, issuer: str, external_subject: str
    ) -> FederatedIdentityMapping | None: ...

    def add_mapping(self, mapping: FederatedIdentityMapping) -> None: ...

    def get_provisioning_record(
        self, *, organization_id: str, external_id: str
    ) -> ProvisioningRecord | None: ...

    def add_provisioning_record(self, record: ProvisioningRecord) -> None: ...

    def update_provisioning_record(self, record: ProvisioningRecord) -> None: ...

    def list_provisioning_records(
        self, *, organization_id: str
    ) -> Sequence[ProvisioningRecord]: ...
