"""Configuration access port.

A small, role-specific read port (ISP, rule 20): the kernel *reads* configuration; it does
not own a config store. Concrete adapters (env, file, secret manager) implement this port
behind the boundary — secrets never live in kernel code (rule 50).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..errors import ConfigurationKeyMissing


@runtime_checkable
class ConfigurationReaderPort(Protocol):
    """Read typed configuration values by key.

    ``get`` returns ``None`` (or the caller default) when absent; ``require`` enforces
    presence and raises :class:`ConfigurationKeyMissing` — deny-by-default for required keys.
    """

    def get(self, key: str, default: str | None = None) -> str | None: ...

    def require(self, key: str) -> str: ...


def require_key(reader: ConfigurationReaderPort, key: str) -> str:
    """Helper enforcing presence of a required key via any reader implementation.

    Kept as a free function so every adapter gets identical deny-by-default semantics
    without re-implementing the check (DRY, rule 21).
    """
    value = reader.get(key)
    if value is None:
        raise ConfigurationKeyMissing(key)
    return value
