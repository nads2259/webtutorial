"""Audit reader adapter: exposes the kernel audit trail to the Studio explorer (read-only).

The Governance Studio never reads another module's persistence and never writes any table. This
adapter wraps the kernel audit recorder's public, append-only trail so the ``studio.audit.explore``
capability can correlate evidence (FR-CMS-006). It performs no writes and imports no SQLAlchemy or
another module's tables — it depends only on the kernel audit contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from northstar.kernel.audit.ports import AuditRecord


class _RecordSource(Protocol):
    @property
    def records(self) -> tuple[AuditRecord, ...]: ...


class RecorderAuditReader:
    """Adapts a kernel audit recorder (exposing ``records``) to the Studio audit reader port."""

    def __init__(self, recorder: _RecordSource) -> None:
        self._recorder = recorder

    def records(self) -> Sequence[AuditRecord]:
        return self._recorder.records
