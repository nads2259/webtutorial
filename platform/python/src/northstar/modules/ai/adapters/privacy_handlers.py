"""AI-memory data-subject-rights handler for the privacy DSAR registry (H03 seam, EVAL-DATA-009).

Bridges the privacy module's ``ExportHandlerPort`` / ``ErasureHandlerPort`` (registered on the
``DataSubjectRightsRegistry``) to the AI module's OWN purpose-limited memory store, so a privacy
DSAR export gathers the subject's AI memory and a privacy erase PROPAGATES into AI memory until the
deletion residue is zero. The privacy ports are structural (``runtime_checkable`` Protocols), so
this adapter satisfies them by shape without importing the privacy module — the AI module keeps
owning its data (LAW-13) and the erase reuses the same authoritative ``erase_for_owner`` path as
``ai.memory.reset``.

The store is infra-free itself: it delegates to the injected :class:`MemoryRepositoryPort` (an
in-memory or SQLAlchemy adapter), so it never reaches a table or secret directly (LAW-09).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..application.ports import MemoryRepositoryPort

STORE_AI_MEMORY = "ai.memory"


class AiMemorySubjectStore:
    """DSAR export + erasure handler for the AI module's per-subject memory (tenant + owner scoped).

    ``export_subject`` returns the subject's full AI-memory history (active + superseded revisions);
    ``erase_subject`` removes ALL of the subject's AI memory and returns the count; and
    ``count_subject`` reports the deletion residue. AI memory has no clock-driven TTL purge here, so
    ``purge_expired`` is a deterministic no-op (returns 0) — the erase path is what a DSAR uses.
    """

    def __init__(
        self, *, repository: MemoryRepositoryPort, store_id: str = STORE_AI_MEMORY
    ) -> None:
        self._repo = repository
        self._store_id = store_id

    @property
    def store_id(self) -> str:
        return self._store_id

    def export_subject(self, *, organization_id: str, subject_id: str) -> Mapping[str, object]:
        records = self._repo.export_for_owner(organization_id=organization_id, owner_id=subject_id)
        return {
            "store_id": self._store_id,
            "count": len(records),
            "items": [
                {
                    "memory_id": record.memory_id,
                    "memory_class": record.memory_class.value,
                    "purpose": record.purpose,
                    "classification": record.classification,
                    "content": record.content,
                    "inferred": record.inferred,
                    "active": record.active,
                    "supersedes": record.supersedes,
                    "superseded_by": record.superseded_by,
                }
                for record in records
            ],
        }

    def erase_subject(self, *, organization_id: str, subject_id: str) -> int:
        return self._repo.erase_for_owner(organization_id=organization_id, owner_id=subject_id)

    def count_subject(self, *, organization_id: str, subject_id: str) -> int:
        return self._repo.count_for_owner(organization_id=organization_id, owner_id=subject_id)

    def purge_expired(self, *, organization_id: str, now: datetime) -> int:
        return 0


__all__ = ["STORE_AI_MEMORY", "AiMemorySubjectStore"]
