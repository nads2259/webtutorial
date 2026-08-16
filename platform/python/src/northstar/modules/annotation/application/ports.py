"""Ports (abstractions) for the annotation application layer (rule 10/20, DIP).

The repository is role-specific and tenant-aware: every read/write is scoped by ``organization_id``
so a caller can never reach another tenant's annotations (rule 50). The snapshot provider is the
read-only seam onto knowledge revisions used for deterministic remapping — the annotation module
reads the published block projection of another module through this port and NEVER writes it
(LAW-13, no cross-module writes).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.model import Annotation, ModerationAction
from ..domain.remap import RevisionSnapshot


@runtime_checkable
class AnnotationRepositoryPort(Protocol):
    """Persists and reads annotations + moderation evidence, always tenant-scoped."""

    def add(self, annotation: Annotation) -> None: ...

    def get(self, *, organization_id: str, annotation_id: str) -> Annotation | None:
        """Return the annotation only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def update(self, annotation: Annotation) -> None:
        """Persist a new state/visibility/remap for an existing annotation (tenant-scoped)."""
        ...

    def list_for_target(self, *, organization_id: str, object_id: str) -> Sequence[Annotation]: ...

    def add_moderation(self, action: ModerationAction) -> None:
        """Append a tamper-evident moderation evidence row (FR-ANN-007, LAW-14)."""
        ...


@runtime_checkable
class RevisionSnapshotProviderPort(Protocol):
    """Read-only projection of a knowledge revision into a pure remap snapshot (LAW-13)."""

    def snapshot(self, *, organization_id: str, revision_id: str) -> RevisionSnapshot | None: ...
