"""Revision snapshot provider: projects knowledge revisions for deterministic remapping (LAW-13).

This adapter is the read-only seam onto the knowledge module. It reads a published revision's block
projection through knowledge's own read model and converts it into a pure
:class:`~northstar.modules.annotation.domain.remap.RevisionSnapshot`. The annotation module NEVER
writes knowledge data (no cross-module writes, LAW-13); it consumes only the published block tree.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..application.ports import RevisionSnapshotProviderPort
from ..domain.remap import RevisionSnapshot, snapshot_from_document_blocks


@runtime_checkable
class KnowledgeRevisionReader(Protocol):
    """The minimal knowledge read surface this provider depends on (a published revision reader)."""

    def get_revision(self, *, organization_id: str, revision_id: str) -> object | None: ...


class KnowledgeRevisionSnapshotProvider(RevisionSnapshotProviderPort):
    """Builds remap snapshots from knowledge revisions (read-only, tenant-scoped)."""

    def __init__(self, *, reader: KnowledgeRevisionReader) -> None:
        self._reader = reader

    def snapshot(self, *, organization_id: str, revision_id: str) -> RevisionSnapshot | None:
        revision = self._reader.get_revision(
            organization_id=organization_id, revision_id=revision_id
        )
        if revision is None:
            return None
        tree = getattr(revision, "tree", None)
        if tree is None:
            return None
        return snapshot_from_document_blocks(revision_id, tree.to_document_blocks())
