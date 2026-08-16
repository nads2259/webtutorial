"""Reference data-subject-rights handlers for a representative set of stores (EVAL-DATA-009).

These adapters implement the export + erasure seams (``ExportHandlerPort`` / ``ErasureHandlerPort``
in :mod:`northstar.modules.privacy.application.ports`) for a REPRESENTATIVE set of heterogeneous
stores — a DB row store (learning progress/overlay, annotation, ai-memory), an object-store blob
seam, a search/retrieval-projection seam, an analytics seam and a provider seam. Registering them on
the ``DataSubjectRightsRegistry`` proves erasure propagates across ALL registered stores until the
deletion residue is zero, and that a data-subject export gathers every store's data. Remaining
modules register their own handlers later through the same registry seam.

The reference implementation is an in-memory, tenant- and subject-scoped store that also honors
clock-controlled retention (``purge_expired``). In production these handlers are backed by the
owning module's capability / real infrastructure adapter; the seam and its contract are identical.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..domain.model import RetainedRecord, RetentionPolicy

# Canonical store identifiers for the representative registered stores (stable vocabulary).
STORE_LEARNING_PROGRESS = "learning.progress"
STORE_LEARNING_OVERLAY = "learning.overlay"
STORE_ANNOTATION = "annotation.notes"
STORE_AI_MEMORY = "ai.memory"
STORE_OBJECTSTORE_BLOBS = "objectstore.blobs"
STORE_SEARCH_PROJECTION = "search.projection"
STORE_ANALYTICS_EVENTS = "analytics.events"
STORE_PROVIDER_EXPORT = "provider.export"

REPRESENTATIVE_STORE_IDS: tuple[str, ...] = (
    STORE_LEARNING_PROGRESS,
    STORE_LEARNING_OVERLAY,
    STORE_ANNOTATION,
    STORE_AI_MEMORY,
    STORE_OBJECTSTORE_BLOBS,
    STORE_SEARCH_PROJECTION,
    STORE_ANALYTICS_EVENTS,
    STORE_PROVIDER_EXPORT,
)


class InMemorySubjectStore:
    """A tenant- + subject-scoped personal-data store behind the DSAR export/erasure seams.

    Holds :class:`RetainedRecord` items per ``(organization_id, subject_id)``. It satisfies both
    export and erasure ports: ``export_subject`` yields a portable section, ``erase_subject`` drops
    every item for the subject (returning the count removed), ``count_subject`` returns the deletion
    residue, and ``purge_expired`` deterministically drops items past their retention against an
    injected ``now`` (NFR-PRV-005). An optional ``default_policy`` sets the retention for records
    added without their own policy, so a store for a stricter class (e.g. private notes) enforces a
    shorter retention.
    """

    def __init__(self, store_id: str, *, default_policy: RetentionPolicy | None = None) -> None:
        self._store_id = store_id
        self._default_policy = default_policy
        self._items: dict[tuple[str, str], list[RetainedRecord]] = {}

    @property
    def store_id(self) -> str:
        return self._store_id

    def seed(
        self,
        *,
        organization_id: str,
        subject_id: str,
        payload: Mapping[str, object],
        created_at: datetime,
        policy: RetentionPolicy | None = None,
    ) -> None:
        """Add one personal-data item for the subject (test/fixture helper)."""
        effective = policy or self._default_policy
        if effective is None:
            raise ValueError(
                f"store {self._store_id!r} needs a retention policy to hold personal data"
            )
        record = RetainedRecord(
            subject_id=subject_id,
            created_at=created_at,
            policy=effective,
            payload=dict(payload),
        )
        self._items.setdefault((organization_id, subject_id), []).append(record)

    def export_subject(self, *, organization_id: str, subject_id: str) -> Mapping[str, object]:
        records = self._items.get((organization_id, subject_id), [])
        return {
            "store_id": self._store_id,
            "count": len(records),
            "items": [dict(record.payload) for record in records],
        }

    def erase_subject(self, *, organization_id: str, subject_id: str) -> int:
        removed = self._items.pop((organization_id, subject_id), [])
        return len(removed)

    def count_subject(self, *, organization_id: str, subject_id: str) -> int:
        return len(self._items.get((organization_id, subject_id), []))

    def purge_expired(self, *, organization_id: str, now: datetime) -> int:
        purged = 0
        for (org, subject), records in list(self._items.items()):
            if org != organization_id:
                continue
            kept = [record for record in records if not record.is_expired(now)]
            purged += len(records) - len(kept)
            if kept:
                self._items[(org, subject)] = kept
            else:
                del self._items[(org, subject)]
        return purged


__all__ = [
    "REPRESENTATIVE_STORE_IDS",
    "STORE_AI_MEMORY",
    "STORE_ANALYTICS_EVENTS",
    "STORE_ANNOTATION",
    "STORE_LEARNING_OVERLAY",
    "STORE_LEARNING_PROGRESS",
    "STORE_OBJECTSTORE_BLOBS",
    "STORE_PROVIDER_EXPORT",
    "STORE_SEARCH_PROJECTION",
    "InMemorySubjectStore",
]
