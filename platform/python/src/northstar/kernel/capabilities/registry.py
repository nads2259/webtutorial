"""Versioned capability registry and dispatcher.

Enforces LAW-04 (one authoritative implementation per capability): a ``(name, version)``
pair may be registered exactly once. Resolution and dispatch are deny-by-default — an
unknown capability raises :class:`UnknownCapability` rather than returning ``None``.

The kernel does not know what a capability *does*; it only routes to a registered
:class:`CapabilityHandler` port. Concrete handlers live in application/adapter layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..errors import DuplicateCapability, UnknownCapability

_CAPABILITY_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


@runtime_checkable
class CapabilityHandler(Protocol):
    """Port a capability implementation must satisfy.

    The kernel invokes ``handle`` with an opaque request and returns an opaque result;
    request/response typing is owned by the capability's own contract, not the kernel.
    """

    def handle(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True)
class Capability:
    """A registered, versioned capability binding name+version to its handler."""

    name: str
    version: str
    handler: CapabilityHandler

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)

    @property
    def coordinate(self) -> str:
        return f"{self.name}@{self.version}"


class CapabilityRegistry:
    """Holds the one authoritative handler per ``(name, version)`` capability."""

    def __init__(self) -> None:
        self._capabilities: dict[tuple[str, str], Capability] = {}

    def register(self, name: str, version: str, handler: CapabilityHandler) -> Capability:
        """Register a versioned capability. Raises on invalid id or duplicate (LAW-04)."""
        if not _CAPABILITY_NAME_RE.match(name):
            raise UnknownCapability(name, version)
        if not _SEMVER_RE.match(version):
            raise UnknownCapability(name, version)
        key = (name, version)
        if key in self._capabilities:
            raise DuplicateCapability(name, version)
        capability = Capability(name=name, version=version, handler=handler)
        self._capabilities[key] = capability
        return capability

    def resolve(self, name: str, version: str) -> Capability:
        """Resolve by exact name+version. Raises :class:`UnknownCapability` if absent."""
        capability = self._capabilities.get((name, version))
        if capability is None:
            raise UnknownCapability(name, version)
        return capability

    def has(self, name: str, version: str) -> bool:
        return (name, version) in self._capabilities

    def versions(self, name: str) -> tuple[str, ...]:
        """Registered versions of ``name``, sorted for determinism."""
        return tuple(sorted(v for (n, v) in self._capabilities if n == name))

    @property
    def coordinates(self) -> tuple[str, ...]:
        return tuple(sorted(c.coordinate for c in self._capabilities.values()))


class CapabilityDispatcher:
    """Resolves a capability by name+version and invokes its handler.

    A single authoritative entry point (LAW-04) so UI/API/CLI/AI all dispatch identically.
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def dispatch(self, name: str, version: str, request: object) -> object:
        """Resolve then invoke. Raises :class:`UnknownCapability` for unknown capability."""
        capability = self._registry.resolve(name, version)
        return capability.handler.handle(request)
