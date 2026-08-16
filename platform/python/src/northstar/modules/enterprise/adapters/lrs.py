"""Reference Learning Record Store (LRS) + export-consent adapters (behind their ports).

``InMemoryLrs`` implements :class:`LrsPort`: it accepts an xAPI-shaped statement, assigns a
statement id and stores the rendered statement. It holds NO first-party learning state — emission
is purely outbound, so disabling this adapter changes nothing about the learning module's own
progress/overlay data (FR-LRN-008 independence). A real xAPI LRS (SCORM Cloud, Learning Locker, …)
is a drop-in swap behind the same port.

``InMemoryExportConsent`` implements :class:`ExportConsentPort` as a deny-by-default gate: a learner
who has not opted in has no export consent, so no statement leaves the platform.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..application.ports import LrsReceipt
from ..domain.model import XapiStatement


class InMemoryLrs:
    """Deterministic in-memory LRS for tests and the reference wiring (outbound only)."""

    def __init__(self, *, id_factory: Any = None) -> None:  # noqa: ANN401 - injectable id source
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._statements: list[tuple[str, dict[str, Any]]] = []

    def emit(self, statement: XapiStatement) -> LrsReceipt:
        statement_id = self._id_factory()
        self._statements.append((statement_id, statement.to_dict()))
        return LrsReceipt(statement_id=statement_id, stored=True)

    @property
    def statements(self) -> list[tuple[str, dict[str, Any]]]:
        """The stored (id, statement) pairs — evidence for independence/shape tests."""
        return list(self._statements)


class InMemoryExportConsent:
    """Deny-by-default export-consent directory (tenant + subject scoped)."""

    def __init__(self) -> None:
        self._consented: set[tuple[str, str]] = set()

    def grant(self, *, organization_id: str, subject_id: str) -> None:
        self._consented.add((organization_id, subject_id))

    def revoke(self, *, organization_id: str, subject_id: str) -> None:
        self._consented.discard((organization_id, subject_id))

    def has_export_consent(self, *, organization_id: str, subject_id: str) -> bool:
        return (organization_id, subject_id) in self._consented
