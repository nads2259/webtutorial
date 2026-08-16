"""Published-content gateway adapters implementing :class:`PublishedContentPort` (FR-LRN-001).

A course composes PUBLISHED knowledge revisions; learning must never copy their bodies (docs/04 §5).
These adapters are the READ-ONLY seam onto the knowledge module (no cross-module writes, LAW-13):

* :class:`KnowledgePublishedContent` reuses the knowledge repository's reads and confirms a revision
  is published (the document's lifecycle is ``published``) before resolving its stable block ids.
* :class:`InMemoryPublishedContent` is a deterministic fake for fast unit/security tests.
"""

from __future__ import annotations

from typing import Protocol

from northstar.modules.knowledge.domain.model import Lifecycle

from ..application.ports import PublishedRevision


class _KnowledgeReader(Protocol):
    """The narrow slice of the knowledge repository this gateway reuses (read-only)."""

    def get_document(self, *, organization_id: str, object_id: str) -> object | None: ...

    def get_revision(self, *, organization_id: str, revision_id: str) -> object | None: ...


class KnowledgePublishedContent:
    """Reference :class:`PublishedContentPort` that reuses the knowledge repository (read-only)."""

    def __init__(self, *, reader: _KnowledgeReader) -> None:
        self._reader = reader

    def published_revision(
        self, *, organization_id: str, object_id: str, revision_id: str
    ) -> PublishedRevision | None:
        document = self._reader.get_document(organization_id=organization_id, object_id=object_id)
        if document is None or getattr(document, "lifecycle", None) is not Lifecycle.PUBLISHED:
            return None
        revision = self._reader.get_revision(
            organization_id=organization_id, revision_id=revision_id
        )
        if revision is None or getattr(revision, "object_id", None) != object_id:
            return None
        tree = getattr(revision, "tree", None)
        blocks = tuple(getattr(b, "block_id", "") for b in getattr(tree, "blocks", ()))
        return PublishedRevision(object_id=object_id, revision_id=revision_id, block_ids=blocks)


class InMemoryPublishedContent:
    """Deterministic fake published-content seam (fast tests).

    Seed published revisions with :meth:`publish`; only seeded revisions are treated as published,
    so a course cannot compose a draft/unknown revision (FR-LRN-001).
    """

    def __init__(self) -> None:
        self._published: dict[tuple[str, str, str], tuple[str, ...]] = {}

    def publish(
        self, *, organization_id: str, object_id: str, revision_id: str, block_ids: tuple[str, ...]
    ) -> None:
        self._published[(organization_id, object_id, revision_id)] = block_ids

    def published_revision(
        self, *, organization_id: str, object_id: str, revision_id: str
    ) -> PublishedRevision | None:
        block_ids = self._published.get((organization_id, object_id, revision_id))
        if block_ids is None:
            return None
        return PublishedRevision(object_id=object_id, revision_id=revision_id, block_ids=block_ids)


__all__ = ["InMemoryPublishedContent", "KnowledgePublishedContent"]
