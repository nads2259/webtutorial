"""Ports (abstractions) for the privacy application layer (rule 10/20, DIP).

Three seams keep the capabilities infrastructure-free and holding no ambient authority (rule 50):

* :class:`PrivacyRepositoryPort` — the privacy module's OWN tenant-scoped persistence for the data
  catalog, the versioned/immutable consent history and the data-subject-rights request lifecycle
  (LAW-13). Consent is append-only: a new decision is a new row, never an update.
* :class:`ExportHandlerPort` / :class:`ErasureHandlerPort` — the seams an owned module (or an
  infrastructure store: object store, search/retrieval projection, analytics, a provider) registers
  on the ``DataSubjectRightsRegistry`` so a data-subject export gathers every store's data and an
  erase PROPAGATES across ALL of them until the deletion residue is zero (EVAL-DATA-009). The erase
  seam also drives clock-controlled retention purging (``purge_expired``) so each store honors its
  own (possibly stricter) retention (NFR-PRV-005).

Handlers implement export and/or erasure for exactly one store; the registry composes them. No
handler is trusted to bypass the registry, and none receives ambient DB/secret access (LAW-15).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.model import (
    ConsentRecord,
    PersonalDataField,
    RightsRequest,
)


@runtime_checkable
class ExportHandlerPort(Protocol):
    """Exports one registered store's personal data for a subject (portable, read-only)."""

    @property
    def store_id(self) -> str:
        """A stable identifier for the store this handler owns (e.g. ``ai.memory``)."""
        ...

    def export_subject(self, *, organization_id: str, subject_id: str) -> Mapping[str, object]:
        """Return the subject's personal data held in this store (intelligible + scoped)."""
        ...


@runtime_checkable
class ErasureHandlerPort(Protocol):
    """Erases (and counts + retention-purges) one registered store's personal data for a subject."""

    @property
    def store_id(self) -> str:
        """A stable identifier for the store this handler owns (e.g. ``objectstore.blobs``)."""
        ...

    def erase_subject(self, *, organization_id: str, subject_id: str) -> int:
        """Erase the subject's personal data in this store; return the number of items removed."""
        ...

    def count_subject(self, *, organization_id: str, subject_id: str) -> int:
        """Return how many personal-data items remain for the subject (the deletion residue)."""
        ...

    def purge_expired(self, *, organization_id: str, now: datetime) -> int:
        """Purge records past retention as of ``now``; return the number purged (NFR-PRV-005)."""
        ...


@runtime_checkable
class PrivacyRepositoryPort(Protocol):
    """Persists/reads the privacy module's OWN tenant-scoped data (rule 50, LAW-13)."""

    # Data catalog ------------------------------------------------------
    def add_field(self, *, organization_id: str, field: PersonalDataField) -> None: ...

    def get_field(self, *, organization_id: str, field_id: str) -> PersonalDataField | None: ...

    def list_fields(self, *, organization_id: str) -> Sequence[PersonalDataField]: ...

    # Versioned, immutable consent history ------------------------------
    def latest_consent(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> ConsentRecord | None: ...

    def add_consent(self, *, organization_id: str, record: ConsentRecord) -> None: ...

    def consent_history(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> Sequence[ConsentRecord]: ...

    # Data-subject-rights request lifecycle -----------------------------
    def add_request(self, *, organization_id: str, request: RightsRequest) -> None: ...

    def get_request(self, *, organization_id: str, request_id: str) -> RightsRequest | None: ...

    def update_request(self, *, organization_id: str, request: RightsRequest) -> None: ...


__all__ = [
    "ErasureHandlerPort",
    "ExportHandlerPort",
    "PrivacyRepositoryPort",
]
