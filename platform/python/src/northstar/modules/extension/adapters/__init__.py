"""Extension adapters (infrastructure allowed here, rule 10): signature verifier, manifest
validation, persistence registry and the in-process capability-dispatch guard."""

from __future__ import annotations

from .dispatch_guard import CapabilityDispatchGuard
from .manifest_validation import JsonSchemaManifestValidator, load_extension_schemas
from .repositories import InMemoryExtensionRegistry, SqlAlchemyExtensionRegistry
from .signature_verifier import HmacSignatureVerifier, PublisherKey, sign_manifest
from .tables import EXTENSION_SCHEMA, ExtensionTables, build_extension_tables

__all__ = [
    "EXTENSION_SCHEMA",
    "CapabilityDispatchGuard",
    "ExtensionTables",
    "HmacSignatureVerifier",
    "InMemoryExtensionRegistry",
    "JsonSchemaManifestValidator",
    "PublisherKey",
    "SqlAlchemyExtensionRegistry",
    "build_extension_tables",
    "load_extension_schemas",
    "sign_manifest",
]
