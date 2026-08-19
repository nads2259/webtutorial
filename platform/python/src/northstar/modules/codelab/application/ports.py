"""Ports for the codelab application layer (DIP, rule 10/20).

The execution seam (:class:`CodeSandboxPort`) and the durable tracking seam
(:class:`CodeRunStorePort`) are abstractions: the reference adapters are a locked-down subprocess and
a PostgreSQL store, but a container/microVM sandbox or a different evidence store can replace either
without touching the domain or the capability handlers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.model import CodeLimits, CodeRun, ExecResult


@runtime_checkable
class CodeSandboxPort(Protocol):
    """Executes untrusted code under resource limits and returns only its observable result."""

    def run(self, *, language: str, code: str, stdin: str, limits: CodeLimits) -> ExecResult: ...


@runtime_checkable
class CodeRunStorePort(Protocol):
    """Persists and reads IMMUTABLE tracked code-run records, always tenant-scoped."""

    def record(self, run: CodeRun) -> None: ...

    def list_for_actor(
        self, *, organization_id: str, actor_id: str, limit: int = 50
    ) -> Sequence[CodeRun]: ...
