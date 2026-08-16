"""Typed configuration facility: declared schema, unknown-key rejection and per-value provenance.

Closes FR-KRN-003 (EVAL-KRN-003). The kernel *reads* configuration through
:class:`~northstar.kernel.configuration.ports.ConfigurationReaderPort`; this module adds a small,
pure (stdlib-only, rule 10) typed layer on top of the raw string readers/sources so that effective
values are:

* **validated** against a declared :class:`ConfigSchema` (type + range/choice constraints);
* **deny-unknown**: a key present in any source but absent from the schema is rejected
  (:class:`~northstar.kernel.errors.UnknownConfigurationKey`) rather than silently ignored;
* **provenance-bearing**: every effective value records the :class:`ConfigProvenance` source
  (``default`` | ``env`` | ``file`` | ``override``) that supplied it, so effective configuration is
  explainable.

Sources are supplied as ordered *layers* of raw string mappings (as an env/file reader yields).
Precedence is highest-layer-wins; a field with no supplied value falls back to its schema default
(provenance ``default``) or, if required and defaultless, raises
:class:`~northstar.kernel.errors.ConfigurationKeyMissing` (deny-by-default for required keys).

The resolved result also exposes a :class:`ConfigurationReaderPort` view
(:meth:`ResolvedConfiguration.as_reader`) whose ``get``/``require`` return the string form of each
effective value, so existing readers keep working unchanged (Liskov, rule 20).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from ..errors import (
    ConfigurationKeyMissing,
    ConfigurationValueInvalid,
    UnknownConfigurationKey,
)
from .ports import ConfigurationReaderPort

ConfigScalar = str | int | float | bool
"""The set of primitive types a typed configuration value can hold (after coercion)."""

__all__ = [
    "ConfigField",
    "ConfigProvenance",
    "ConfigScalar",
    "ConfigSchema",
    "ConfigType",
    "ConfigValue",
    "ConfigurationReaderView",
    "ResolvedConfiguration",
    "resolve_configuration",
]


class ConfigType(Enum):
    """The declared primitive type of a configuration value (parsed from its raw string form)."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"


class ConfigProvenance(Enum):
    """The source layer that supplied an effective value (least → most authoritative)."""

    DEFAULT = "default"
    FILE = "file"
    ENV = "env"
    OVERRIDE = "override"


_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ConfigField:
    """A single declared configuration key: its type, optionality and range/choice constraints.

    ``minimum``/``maximum`` bound numeric (``INTEGER``/``FLOAT``) values inclusively; ``choices``
    restricts the *parsed* value to an explicit allowlist (any type). ``default`` is the effective
    value (already typed) when no source supplies the key; a field with ``required=True`` and no
    ``default`` must be supplied by some source or resolution fails deny-by-default.
    """

    key: str
    type: ConfigType
    default: ConfigScalar | None = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[ConfigScalar, ...] | None = None
    description: str = ""

    def _coerce(self, raw: str, source: str) -> ConfigScalar:
        """Parse ``raw`` (a string from a source layer) into this field's declared type."""
        text = raw.strip()
        if self.type is ConfigType.STRING:
            return raw
        if self.type is ConfigType.BOOLEAN:
            lowered = text.lower()
            if lowered in _TRUE_TOKENS:
                return True
            if lowered in _FALSE_TOKENS:
                return False
            raise ConfigurationValueInvalid(
                self.key, source, f"'{raw}' is not a boolean (expected true/false)"
            )
        try:
            return int(text) if self.type is ConfigType.INTEGER else float(text)
        except ValueError as err:
            raise ConfigurationValueInvalid(
                self.key, source, f"'{raw}' is not a valid {self.type.value}"
            ) from err

    def _validate(self, value: ConfigScalar, source: str) -> ConfigScalar:
        """Enforce range/choice constraints on an already-typed ``value`` (default or coerced)."""
        if self.type in (ConfigType.INTEGER, ConfigType.FLOAT):
            if self.minimum is not None and value < self.minimum:
                raise ConfigurationValueInvalid(
                    self.key, source, f"{value} is below the minimum {self.minimum}"
                )
            if self.maximum is not None and value > self.maximum:
                raise ConfigurationValueInvalid(
                    self.key, source, f"{value} is above the maximum {self.maximum}"
                )
        if self.choices is not None and value not in self.choices:
            allowed = ", ".join(str(c) for c in self.choices)
            raise ConfigurationValueInvalid(
                self.key, source, f"{value!r} is not one of the allowed choices ({allowed})"
            )
        return value

    def resolve(self, raw: str, source: str) -> ConfigScalar:
        """Coerce ``raw`` to the declared type and validate it (used for a supplied value)."""
        return self._validate(self._coerce(raw, source), source)


@dataclass(frozen=True, slots=True)
class ConfigValue:
    """An effective configuration value plus the provenance that determined it."""

    key: str
    value: ConfigScalar
    provenance: ConfigProvenance

    def as_str(self) -> str:
        """Render the effective value back to its canonical string form (for reader views)."""
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ConfigSchema:
    """An immutable set of declared :class:`ConfigField` entries keyed by ``key``."""

    fields: Mapping[str, ConfigField]

    @classmethod
    def of(cls, *fields: ConfigField) -> ConfigSchema:
        """Build a schema from field declarations, rejecting a duplicate key."""
        mapping: dict[str, ConfigField] = {}
        for f in fields:
            if f.key in mapping:
                raise ValueError(f"duplicate configuration field '{f.key}'")
            mapping[f.key] = f
        return cls(fields=mapping)

    def __contains__(self, key: object) -> bool:
        return key in self.fields


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    """The effective, typed, provenance-bearing configuration produced by resolution."""

    values: Mapping[str, ConfigValue] = field(default_factory=dict)

    def get(self, key: str, default: ConfigScalar | None = None) -> ConfigScalar | None:
        """Return the effective typed value for ``key`` (or ``default`` when absent)."""
        entry = self.values.get(key)
        return entry.value if entry is not None else default

    def provenance(self, key: str) -> ConfigProvenance:
        """Return which source layer supplied ``key``; raises if the key was not resolved."""
        entry = self.values.get(key)
        if entry is None:
            raise ConfigurationKeyMissing(key)
        return entry.provenance

    def as_reader(self) -> ConfigurationReaderView:
        """Expose the resolved values through the existing :class:`ConfigurationReaderPort`."""
        return ConfigurationReaderView(self)


class ConfigurationReaderView(ConfigurationReaderPort):
    """A :class:`ConfigurationReaderPort` backed by a :class:`ResolvedConfiguration`.

    Returns the string form of each effective value so callers written against the existing
    string port keep working, while richer callers can use the typed :class:`ResolvedConfiguration`.
    """

    def __init__(self, resolved: ResolvedConfiguration) -> None:
        self._resolved = resolved

    def get(self, key: str, default: str | None = None) -> str | None:
        entry = self._resolved.values.get(key)
        return entry.as_str() if entry is not None else default

    def require(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            raise ConfigurationKeyMissing(key)
        return value


def resolve_configuration(
    schema: ConfigSchema,
    layers: Sequence[tuple[ConfigProvenance, Mapping[str, str]]],
) -> ResolvedConfiguration:
    """Resolve ``layers`` (ordered least→most authoritative) against ``schema``.

    Deterministic: for a given schema + layers the result is identical. Rejects any key that is not
    declared in the schema (:class:`UnknownConfigurationKey`), coerces + validates each supplied
    value against its field (:class:`ConfigurationValueInvalid`), records the winning
    :class:`ConfigProvenance` per value, and applies the field default (provenance ``default``) or
    fails for a required, defaultless, unsupplied key (:class:`ConfigurationKeyMissing`).
    """
    # Deny-unknown first: any key in any layer that the schema does not declare is rejected.
    for provenance, mapping in layers:
        for key in mapping:
            if key not in schema.fields:
                raise UnknownConfigurationKey(key, provenance.value)

    resolved: dict[str, ConfigValue] = {}
    for key, spec in schema.fields.items():
        winner: ConfigValue | None = None
        for provenance, mapping in layers:
            if key in mapping:
                value = spec.resolve(mapping[key], provenance.value)
                winner = ConfigValue(key=key, value=value, provenance=provenance)
        if winner is None:
            if spec.default is not None:
                validated = spec._validate(spec.default, ConfigProvenance.DEFAULT.value)
                winner = ConfigValue(key=key, value=validated, provenance=ConfigProvenance.DEFAULT)
            elif spec.required:
                raise ConfigurationKeyMissing(key)
            else:
                continue
        resolved[key] = winner
    return ResolvedConfiguration(values=resolved)
