"""Ports (abstractions) for the knowledge application layer (rule 10/20, DIP).

The repository port is role-specific and tenant-aware: every read/write is scoped by
``organization_id`` so a caller can never reach another tenant's documents (rule 50). Publishing
writes the immutable revision **and** appends the domain event to the transactional outbox in the
*same* unit of work (LAW-10), so an event is never emitted without the committed revision.

:class:`ObjectStoragePort` is the media seam (FR-CNT-009): a filesystem/in-memory reference
adapter implements it now; an S3 adapter can replace it later without touching the domain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from northstar.kernel.events.domain_event import DomainEvent

from ..domain.model import (
    Draft,
    KnowledgeObject,
    Publication,
    Revision,
    TaxonomyAssignment,
)


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """A published-document catalog row for browse/list reads (title from the latest revision)."""

    object_id: str
    revision_id: str | None
    title: str
    summary: str | None
    document_type: str
    locale: str
    terms: dict[str, list[str]] = field(default_factory=dict)
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TermCount:
    """A distinct taxonomy term and how many documents carry it (for browse facets)."""

    term: str
    count: int


@runtime_checkable
class KnowledgeRepositoryPort(Protocol):
    """Persists and reads knowledge documents/drafts/revisions/taxonomy, always tenant-scoped."""

    def add_document(self, document: KnowledgeObject) -> None: ...

    def get_document(self, *, organization_id: str, object_id: str) -> KnowledgeObject | None:
        """Return the document only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def save_draft(self, *, organization_id: str, draft: Draft) -> None: ...

    def get_draft(self, *, organization_id: str, object_id: str) -> Draft | None: ...

    def set_lifecycle(self, *, organization_id: str, object_id: str, lifecycle: str) -> None: ...

    def publish(
        self,
        *,
        organization_id: str,
        document: KnowledgeObject,
        revision: Revision,
        publication: Publication,
        event: DomainEvent,
    ) -> None:
        """Atomically write the immutable revision + publication, update the document's lifecycle
        and pointer, and append ``event`` to the transactional outbox — all in one unit of work
        (LAW-07/LAW-10). Re-publishing an existing ``revision_id`` is rejected (immutability)."""
        ...

    def get_revision(self, *, organization_id: str, revision_id: str) -> Revision | None: ...

    def assign_taxonomy(self, *, organization_id: str, assignment: TaxonomyAssignment) -> None: ...

    def list_taxonomy(
        self, *, organization_id: str, object_id: str
    ) -> Sequence[TaxonomyAssignment]: ...

    def list_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        limit: int = 200,
        offset: int = 0,
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        sort: str = "order",
    ) -> Sequence[CatalogRow]:
        """Published documents in this tenant, optionally filtered by taxonomy ``scheme=term``.

        Ordered by the ``order`` taxonomy term (then title), or by latest publication time when
        ``sort='recent'``. Title and published-at filters are applied in the database.
        """
        ...

    def count_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
    ) -> int:
        """Count published documents matching the same filters as :meth:`list_published`."""
        ...

    def distinct_terms(self, *, organization_id: str, scheme: str) -> Sequence[TermCount]:
        """Distinct taxonomy terms (with document counts) for ``scheme`` in this tenant."""
        ...


@runtime_checkable
class ObjectStoragePort(Protocol):
    """A minimal object store for media artifacts (FR-CNT-009): put/get/exists by key.

    Keys are opaque, tenant-prefixed strings chosen by the caller. Implementations MUST treat the
    stored bytes as opaque and MUST NOT interpret or transform them.
    """

    def put(self, *, key: str, data: bytes, content_type: str) -> str:
        """Store ``data`` under ``key`` and return the stored object's key (idempotent by key)."""
        ...

    def get(self, *, key: str) -> bytes | None:
        """Return the stored bytes for ``key`` (or ``None`` if absent)."""
        ...

    def exists(self, *, key: str) -> bool: ...
