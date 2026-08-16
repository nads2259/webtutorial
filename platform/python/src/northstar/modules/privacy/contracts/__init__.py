"""Privacy published contract identifiers (rule 40, LAW-11).

The privacy module conforms to the framework's canonical module + migration manifests
(``spec/contracts/schemas/module-manifest.schema.json`` and ``migration-manifest.schema.json``).
DSAR request/response payloads are typed application DTOs in
:mod:`northstar.modules.privacy.application.capabilities`; when a wire contract for the
data-subject export bundle is registered it will be referenced here (backlog B-DOMAIN, rule 40).
"""

from __future__ import annotations

CONTRACT_MODULE_MANIFEST = "module-manifest/1.0.0"
CONTRACT_MIGRATION_MANIFEST = "migration-manifest/1.0.0"

PRIVACY_CONTRACTS: tuple[str, ...] = (
    CONTRACT_MODULE_MANIFEST,
    CONTRACT_MIGRATION_MANIFEST,
)

__all__ = [
    "CONTRACT_MIGRATION_MANIFEST",
    "CONTRACT_MODULE_MANIFEST",
    "PRIVACY_CONTRACTS",
]
