"""Kernel health/version ports and result value objects (FR-KRN-006)."""

from __future__ import annotations

from .ports import (
    HealthProbePort,
    HealthReport,
    HealthState,
    VersionInfo,
    VersionPort,
)

__all__ = [
    "HealthProbePort",
    "HealthReport",
    "HealthState",
    "VersionInfo",
    "VersionPort",
]
