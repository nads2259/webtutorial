"""Codelab: run learner-submitted code in a server-side sandbox, with durable, tracked evidence.

A first-class framework module (hexagonal, ports & adapters). The single authoritative action
``codelab.run.execute`` runs code behind a :class:`CodeSandboxPort` (a locked-down subprocess in the
reference adapter; a container/microVM/nsjail is a drop-in swap) and records an IMMUTABLE, tracked
:class:`CodeRun` through a :class:`CodeRunStorePort`. Every run is also audited by the kernel command
bus. ``codelab.run.list`` reads the caller's own tracked runs.
"""

from __future__ import annotations

from .application.capabilities import (
    CAP_LIST_RUNS,
    CAP_RUN,
    CAP_VERSION,
    CODELAB_CAPABILITIES,
    ListRuns,
    ListRunsQuery,
    RunCode,
    RunCodeCommand,
)

__all__ = [
    "CAP_LIST_RUNS",
    "CAP_RUN",
    "CAP_VERSION",
    "CODELAB_CAPABILITIES",
    "ListRuns",
    "ListRunsQuery",
    "RunCode",
    "RunCodeCommand",
]
