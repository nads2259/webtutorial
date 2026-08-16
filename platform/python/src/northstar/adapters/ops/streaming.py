"""Cooperative cancellation for long AI/media/simulation work streams (EVAL-PERF-003).

Long work MUST expose streaming/progress, cancellation and bounded resource use rather than
holding an opaque synchronous request (docs/18 §5, NFR-PERF-003). This module provides a
representative long-running async stream and an explicit :class:`ResourceLedger` so a test can
prove that cancelling mid-stream (a) stops emitting promptly at the next cooperative await point
and (b) releases every acquired resource, leaving no dangling state. Resources are always released
in a ``finally`` block, so the invariant ``leaked == 0`` holds on normal completion, on cancellation
and on error alike. Stdlib ``asyncio`` only — no provider SDK, DB or HTTP.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


class ResourceLedger:
    """Tracks resource acquire/release so a leak (dangling state) is observable in a test."""

    def __init__(self) -> None:
        self._open: dict[str, int] = {}
        self.acquired = 0
        self.released = 0

    def acquire(self, name: str) -> None:
        self._open[name] = self._open.get(name, 0) + 1
        self.acquired += 1

    def release(self, name: str) -> None:
        current = self._open.get(name, 0)
        if current <= 0:
            raise RuntimeError(f"released a resource that was never acquired: {name!r}")
        self._open[name] = current - 1
        self.released += 1

    @property
    def leaked(self) -> int:
        """Number of still-open resources (0 means every acquisition was released)."""
        return self.acquired - self.released


@dataclass(slots=True)
class StreamProgress:
    """Observable progress/terminal-state of a long stream (for evidence + assertions)."""

    chunks_emitted: int = 0
    completed: bool = False
    cancelled: bool = False
    resources_leaked: int = field(default=0)


class LongRunningStream:
    """A representative long AI-work stream that supports cooperative cancellation.

    Emits ``total_chunks`` chunks, awaiting between each (the cooperative cancellation point). It
    acquires a resource on entry and releases it in a ``finally`` block, so an
    :class:`asyncio.CancelledError` raised at an await point unwinds through the release and leaves
    the :class:`ResourceLedger` balanced.
    """

    def __init__(
        self,
        *,
        total_chunks: int,
        ledger: ResourceLedger,
        progress: StreamProgress,
        chunk_delay_s: float = 0.0,
        resource_name: str = "ai.stream.buffer",
    ) -> None:
        if total_chunks <= 0:
            raise ValueError("total_chunks must be positive")
        if chunk_delay_s < 0:
            raise ValueError("chunk_delay_s must not be negative")
        self._total = total_chunks
        self._ledger = ledger
        self._progress = progress
        self._chunk_delay_s = chunk_delay_s
        self._resource_name = resource_name

    async def stream(self) -> AsyncIterator[str]:
        """Yield chunks until exhausted or cancelled; always release the acquired resource."""
        self._ledger.acquire(self._resource_name)
        try:
            for index in range(self._total):
                # Cooperative cancellation point: cancelling the consuming task raises here.
                await asyncio.sleep(self._chunk_delay_s)
                self._progress.chunks_emitted = index + 1
                yield f"chunk-{index}"
            self._progress.completed = True
        except asyncio.CancelledError:
            self._progress.cancelled = True
            raise
        finally:
            self._ledger.release(self._resource_name)
            self._progress.resources_leaked = self._ledger.leaked
