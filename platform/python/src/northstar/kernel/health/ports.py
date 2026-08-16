"""Health and version ports (FR-KRN-006).

Exposes liveness / readiness / startup probes plus framework version and schema-compatibility
info via small typed Protocols. Result value objects are frozen and serialisation-agnostic —
the kernel reports health as data; the HTTP/ops adapter projects it to a wire format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class HealthState(StrEnum):
    """Outcome of a single health probe."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

    @property
    def is_serving(self) -> bool:
        """Whether traffic should be served in this state (degraded still serves)."""
        return self is not HealthState.UNHEALTHY


@dataclass(frozen=True, slots=True)
class HealthReport:
    """A single probe result: state plus an explainable detail."""

    state: HealthState
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.state.is_serving


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Framework/version and schema-compatibility report."""

    framework_version: str
    contract_api: str
    schema_compatible: bool


@runtime_checkable
class HealthProbePort(Protocol):
    """Liveness / readiness / startup probes.

    * ``liveness`` — the process is running and not deadlocked.
    * ``readiness`` — dependencies are ready; safe to route traffic.
    * ``startup`` — one-time initialization/migrations completed.
    """

    def liveness(self) -> HealthReport: ...

    def readiness(self) -> HealthReport: ...

    def startup(self) -> HealthReport: ...


@runtime_checkable
class VersionPort(Protocol):
    """Reports framework version and whether the active schema/contract API is compatible."""

    def version(self) -> VersionInfo: ...
