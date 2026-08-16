"""JSON-Schema manifest validation against the canonical contracts (FR-EXT-001/002, rule 40).

Manifests are validated against the read-only ``spec/contracts/schemas/*`` sources (the single
source of truth for contracts). The validator is deny-by-default: a document that does not conform
is rejected with an explainable :class:`ManifestInvalid`, never silently coerced. Schemas are
injected so the module stays decoupled from the spec layout (mirrors the governance-studio
contribution registry).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import jsonschema

from ..application.ports import ManifestKind, ManifestValidatorPort
from ..domain.errors import ManifestInvalid

_SCHEMA_FILES: dict[ManifestKind, str] = {
    ManifestKind.PLUGIN: "plugin-manifest.schema.json",
    ManifestKind.THEME: "theme-manifest.schema.json",
    ManifestKind.THEME_TOKEN: "theme-token.schema.json",
    ManifestKind.CAPABILITY_CONTRACT: "capability-contract.schema.json",
}


def _repo_root() -> Path:
    # .../northstar/modules/extension/adapters/manifest_validation.py -> parents[7] == repo root.
    return Path(__file__).resolve().parents[7]


def load_extension_schemas() -> dict[ManifestKind, Mapping[str, object]]:
    """Load the canonical extension manifest schemas from the read-only ``spec/`` tree."""
    schema_dir = _repo_root() / "spec" / "contracts" / "schemas"
    schemas: dict[ManifestKind, Mapping[str, object]] = {}
    for kind, filename in _SCHEMA_FILES.items():
        with (schema_dir / filename).open(encoding="utf-8") as fh:
            schemas[kind] = json.load(fh)
    return schemas


class JsonSchemaManifestValidator(ManifestValidatorPort):
    """Validates manifest documents against injected canonical JSON Schemas (Draft 2020-12)."""

    def __init__(self, schemas: Mapping[ManifestKind, Mapping[str, object]]) -> None:
        self._validators = {
            kind: jsonschema.Draft202012Validator(dict(schema)) for kind, schema in schemas.items()
        }

    def validate(self, kind: ManifestKind, document: Mapping[str, object]) -> None:
        validator = self._validators.get(kind)
        if validator is None:
            raise ManifestInvalid(f"no schema registered for '{kind.value}'")
        errors = sorted(validator.iter_errors(dict(document)), key=lambda e: list(e.absolute_path))
        if errors:
            issues = tuple(
                f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
                for err in errors
            )
            raise ManifestInvalid(f"{kind.value} document failed schema validation", issues=issues)
