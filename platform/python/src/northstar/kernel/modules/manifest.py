"""Typed module-manifest value objects and a stdlib-only validator.

Maps the fields of ``spec/contracts/schemas/module-manifest.schema.json`` (manifest 1.0)
onto frozen dataclasses. The kernel stays import-clean (LAW-02): YAML/JSON parsing happens
at the boundary, so :meth:`ModuleManifest.from_dict` accepts an already-parsed mapping.

The validator is intentionally minimal and deterministic — it mirrors the schema's required
fields and patterns and raises :class:`~northstar.kernel.errors.ManifestInvalid` carrying
*every* issue found (sorted), rather than failing on the first one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..errors import ManifestInvalid

MANIFEST_VERSION = "1.0"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")

_UNINSTALL_POLICIES = frozenset({"retain", "export_then_delete", "delete", "not_applicable"})
_DATA_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})


@dataclass(frozen=True, slots=True)
class FrameworkCompatibility:
    """Framework version window a module supports (schema ``framework_compatibility``)."""

    minimum: str
    contract_api: str
    maximum_exclusive: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleDependency:
    """A declared dependency on another module (schema ``dependencies[]``)."""

    module_id: str
    version_range: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class DataOwnership:
    """Data owned by the module (schema ``data_ownership``)."""

    postgres_schemas: tuple[str, ...] = ()
    object_prefixes: tuple[str, ...] = ()
    classifications: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """Lifecycle declaration (schema ``lifecycle``)."""

    migrations: bool
    health_checks: tuple[str, ...] = ()
    uninstall_policy: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """A validated, immutable module manifest value object.

    Construct via :meth:`from_dict`, which enforces the manifest-1.0 contract. Direct
    construction is allowed for tests/adapters that already hold trusted, typed values.
    """

    module_id: str
    name: str
    version: str
    framework_compatibility: FrameworkCompatibility
    capabilities: tuple[str, ...]
    lifecycle: Lifecycle
    manifest_version: str = MANIFEST_VERSION
    description: str | None = None
    owner: str | None = None
    maturity: str | None = None
    dependencies: tuple[ModuleDependency, ...] = ()
    data_ownership: DataOwnership | None = None

    @property
    def required_dependencies(self) -> tuple[str, ...]:
        """Module ids this manifest hard-depends on (optional deps excluded), sorted."""
        return tuple(sorted(d.module_id for d in self.dependencies if not d.optional))

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ModuleManifest:
        """Validate an already-parsed manifest mapping and build the value object.

        Raises :class:`ManifestInvalid` with the full, deterministically-sorted list of
        issues when the mapping violates the manifest-1.0 contract.
        """
        issues: list[str] = []

        if not isinstance(data, Mapping):
            raise ManifestInvalid("manifest must be a mapping", ("manifest: expected an object",))

        manifest_version = data.get("manifest_version")
        if manifest_version != MANIFEST_VERSION:
            issues.append(
                f"manifest_version: expected '{MANIFEST_VERSION}', got {manifest_version!r}"
            )

        module_id = _require_str(data, "module_id", issues, _MODULE_ID_RE)
        name = _require_str(data, "name", issues, min_len=3, max_len=120)
        version = _require_str(data, "version", issues, _SEMVER_RE)

        framework = _parse_framework(data.get("framework_compatibility"), issues)
        capabilities = _parse_capabilities(data.get("capabilities"), issues)
        dependencies = _parse_dependencies(data.get("dependencies"), issues)
        data_ownership = _parse_data_ownership(data.get("data_ownership"), issues)
        lifecycle = _parse_lifecycle(data.get("lifecycle"), issues)

        maturity = data.get("maturity")
        if maturity is not None and not isinstance(maturity, str):
            issues.append("maturity: expected a string")

        if issues:
            raise ManifestInvalid(
                f"manifest for {module_id or '<unknown>'} is invalid",
                tuple(sorted(issues)),
            )

        # Unreachable unless issues were raised above; narrow for type-checkers.
        if (
            module_id is None
            or name is None
            or version is None
            or framework is None
            or lifecycle is None
        ):  # pragma: no cover
            raise ManifestInvalid("manifest is invalid", tuple(sorted(issues)))
        return cls(
            module_id=module_id,
            name=name,
            version=version,
            framework_compatibility=framework,
            capabilities=capabilities,
            lifecycle=lifecycle,
            manifest_version=MANIFEST_VERSION,
            description=_opt_str(data.get("description")),
            owner=_opt_str(data.get("owner")),
            maturity=_opt_str(maturity),
            dependencies=dependencies,
            data_ownership=data_ownership,
        )


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _require_str(
    data: Mapping[str, object],
    key: str,
    issues: list[str],
    pattern: re.Pattern[str] | None = None,
    *,
    min_len: int | None = None,
    max_len: int | None = None,
) -> str | None:
    value = data.get(key)
    if not isinstance(value, str):
        issues.append(f"{key}: required string is missing")
        return None
    if pattern is not None and not pattern.match(value):
        issues.append(f"{key}: {value!r} does not match required format")
    if min_len is not None and len(value) < min_len:
        issues.append(f"{key}: must have at least {min_len} characters")
    if max_len is not None and len(value) > max_len:
        issues.append(f"{key}: must have at most {max_len} characters")
    return value


def _parse_framework(value: object, issues: list[str]) -> FrameworkCompatibility | None:
    if not isinstance(value, Mapping):
        issues.append("framework_compatibility: required object is missing")
        return None
    minimum = value.get("minimum")
    contract_api = value.get("contract_api")
    maximum = value.get("maximum_exclusive")
    ok = True
    if not isinstance(minimum, str) or not _SEMVER_RE.match(minimum):
        issues.append("framework_compatibility.minimum: required semver is missing")
        ok = False
    if not isinstance(contract_api, str) or not _SEMVER_RE.match(contract_api):
        issues.append("framework_compatibility.contract_api: required semver is missing")
        ok = False
    if maximum is not None and not isinstance(maximum, str):
        issues.append("framework_compatibility.maximum_exclusive: expected string or null")
        ok = False
    if not ok or not isinstance(minimum, str) or not isinstance(contract_api, str):
        return None
    return FrameworkCompatibility(
        minimum=minimum,
        contract_api=contract_api,
        maximum_exclusive=maximum if isinstance(maximum, str) else None,
    )


def _parse_capabilities(value: object, issues: list[str]) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        issues.append("capabilities: required non-empty array is missing")
        return ()
    if len(value) < 1:
        issues.append("capabilities: at least one capability is required")
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _CAPABILITY_RE.match(item):
            issues.append(f"capabilities: {item!r} is not a valid capability name")
            continue
        if item in seen:
            issues.append(f"capabilities: duplicate capability {item!r}")
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _parse_dependencies(value: object, issues: list[str]) -> tuple[ModuleDependency, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        issues.append("dependencies: expected an array")
        return ()
    result: list[ModuleDependency] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            issues.append("dependencies[]: each dependency must be an object")
            continue
        dep_id = item.get("module_id")
        version_range = item.get("version_range")
        optional = item.get("optional", False)
        if not isinstance(dep_id, str) or not _MODULE_ID_RE.match(dep_id):
            issues.append(f"dependencies[]: invalid module_id {dep_id!r}")
            continue
        if not isinstance(version_range, str) or not version_range:
            issues.append(f"dependencies[{dep_id}]: version_range is required")
            continue
        if not isinstance(optional, bool):
            issues.append(f"dependencies[{dep_id}]: optional must be a boolean")
            continue
        if dep_id in seen:
            issues.append(f"dependencies: duplicate dependency on {dep_id!r}")
            continue
        seen.add(dep_id)
        result.append(
            ModuleDependency(module_id=dep_id, version_range=version_range, optional=optional)
        )
    return tuple(result)


def _parse_data_ownership(value: object, issues: list[str]) -> DataOwnership | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        issues.append("data_ownership: expected an object")
        return None
    schemas = _str_array(value.get("postgres_schemas"), "postgres_schemas", issues)
    prefixes = _str_array(value.get("object_prefixes"), "object_prefixes", issues)
    classifications = _str_array(value.get("classifications"), "classifications", issues)
    for classification in classifications:
        if classification not in _DATA_CLASSIFICATIONS:
            issues.append(f"data_ownership.classifications: unknown value {classification!r}")
    return DataOwnership(
        postgres_schemas=schemas,
        object_prefixes=prefixes,
        classifications=classifications,
    )


def _parse_lifecycle(value: object, issues: list[str]) -> Lifecycle | None:
    if not isinstance(value, Mapping):
        issues.append("lifecycle: required object is missing")
        return None
    migrations = value.get("migrations")
    if not isinstance(migrations, bool):
        issues.append("lifecycle.migrations: required boolean is missing")
    health_checks = _str_array(value.get("health_checks"), "lifecycle.health_checks", issues)
    uninstall_policy = value.get("uninstall_policy")
    if uninstall_policy is not None:
        if not isinstance(uninstall_policy, str):
            issues.append("lifecycle.uninstall_policy: expected a string")
            uninstall_policy = None
        elif uninstall_policy not in _UNINSTALL_POLICIES:
            issues.append(f"lifecycle.uninstall_policy: unknown value {uninstall_policy!r}")
    if not isinstance(migrations, bool):
        return None
    return Lifecycle(
        migrations=migrations,
        health_checks=health_checks,
        uninstall_policy=uninstall_policy if isinstance(uninstall_policy, str) else None,
    )


def _str_array(value: object, key: str, issues: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        issues.append(f"{key}: expected an array of strings")
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            issues.append(f"{key}: expected string items")
            continue
        result.append(item)
    return tuple(result)
