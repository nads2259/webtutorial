"""Kernel configuration ports + the typed, provenance-bearing configuration facility."""

from __future__ import annotations

from .ports import ConfigurationReaderPort, require_key
from .schema import (
    ConfigField,
    ConfigProvenance,
    ConfigSchema,
    ConfigType,
    ConfigurationReaderView,
    ConfigValue,
    ResolvedConfiguration,
    resolve_configuration,
)

__all__ = [
    "ConfigField",
    "ConfigProvenance",
    "ConfigSchema",
    "ConfigType",
    "ConfigValue",
    "ConfigurationReaderPort",
    "ConfigurationReaderView",
    "ResolvedConfiguration",
    "require_key",
    "resolve_configuration",
]
