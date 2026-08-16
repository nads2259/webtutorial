"""Kernel capability registry and dispatcher (one authoritative implementation, LAW-04)."""

from __future__ import annotations

from .registry import (
    Capability,
    CapabilityDispatcher,
    CapabilityHandler,
    CapabilityRegistry,
)

__all__ = [
    "Capability",
    "CapabilityDispatcher",
    "CapabilityHandler",
    "CapabilityRegistry",
]
