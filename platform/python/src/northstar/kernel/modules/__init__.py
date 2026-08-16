"""Kernel module runtime: typed manifests and the dependency-ordered module registry."""

from __future__ import annotations

from .manifest import (
    DataOwnership,
    FrameworkCompatibility,
    Lifecycle,
    ModuleDependency,
    ModuleManifest,
)
from .registry import ActivationPlan, ModuleRegistry

__all__ = [
    "ActivationPlan",
    "DataOwnership",
    "FrameworkCompatibility",
    "Lifecycle",
    "ModuleDependency",
    "ModuleManifest",
    "ModuleRegistry",
]
