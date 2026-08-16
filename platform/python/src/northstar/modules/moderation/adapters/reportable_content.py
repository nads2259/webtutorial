"""Reportable-content adapters: the read-only seam onto annotations/comments (LAW-13).

Moderation references reportable content (an annotation or comment) through
:class:`ReportableContentPort` and NEVER reaches its tables (no cross-module reads of internals).
The in-memory provider backs unit/security tests; the annotation-backed provider reads the
annotation module's own read model through a minimal reader protocol to resolve the affected author.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..application.ports import ReportableContent

# Content types this module accepts as reportable (annotations and their comment replies).
_ANNOTATION_CONTENT_TYPES = frozenset({"annotation", "comment"})


class InMemoryReportableContent:
    """In-memory reportable-content directory for deterministic tests."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], str] = {}

    def register(
        self, *, organization_id: str, content_type: str, content_id: str, author_id: str
    ) -> None:
        self._by_key[(organization_id, content_type, content_id)] = author_id

    def describe(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ReportableContent | None:
        author_id = self._by_key.get((organization_id, content_type, content_id))
        if author_id is None:
            return None
        return ReportableContent(
            content_type=content_type, content_id=content_id, author_id=author_id
        )


@runtime_checkable
class AnnotationReader(Protocol):
    """The minimal annotation read surface this provider depends on (a tenant-scoped getter)."""

    def get(self, *, organization_id: str, annotation_id: str) -> object | None: ...


class AnnotationReportableContentProvider:
    """Resolves reportable content from the annotation module's read model (read-only, LAW-13)."""

    def __init__(self, *, reader: AnnotationReader) -> None:
        self._reader = reader

    def describe(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ReportableContent | None:
        if content_type not in _ANNOTATION_CONTENT_TYPES:
            return None
        annotation = self._reader.get(organization_id=organization_id, annotation_id=content_id)
        if annotation is None:
            return None
        creator = getattr(annotation, "creator", None)
        author_id = getattr(creator, "id", None)
        if not author_id:
            return None
        return ReportableContent(
            content_type=content_type, content_id=content_id, author_id=author_id
        )
