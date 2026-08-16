"""Deterministic reference seed for the framework registry (bootstrap step 8).

Idempotent by construction (rule 80, one-touch contract §8): every insert is
``ON CONFLICT DO NOTHING`` on the row's primary key, so re-running the seed never creates a
duplicate and a second bootstrap still reports ``pass``. The data is a small, fixed reference
set — the kernel framework module plus the sample vertical-slice capabilities and the
``cli-output`` contract — that mirrors identifiers already used by the kernel (no invented
capabilities/contracts). Infrastructure (SQLAlchemy) is allowed in this adapter layer (rule 10);
all statements are parameterised (no string-built SQL, rule "secure implementation").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import Engine, text

FRAMEWORK_MODULE_ID = "northstar.kernel"
FRAMEWORK_MODULE_VERSION = "0.3.0"

_KERNEL_MANIFEST = {
    "manifest_version": "1.0",
    "module_id": FRAMEWORK_MODULE_ID,
    "name": "Northstar Kernel",
    "version": FRAMEWORK_MODULE_VERSION,
    "maturity": "beta",
    "capabilities": ["sample.notes.record", "sample.notes.read"],
}


def _sha256_of(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ModuleRow:
    module_id: str
    version: str
    maturity: str
    manifest: dict[str, object]


@dataclass(frozen=True)
class _CapabilityRow:
    capability_id: str
    module_id: str
    contract_version: str
    command_handler: str | None
    query_handler: str | None
    policy_action: str


@dataclass(frozen=True)
class _ContractRow:
    contract_id: str
    version: str
    kind: str
    schema_uri: str
    compatibility: str


_MODULES: tuple[_ModuleRow, ...] = (
    _ModuleRow(
        module_id=FRAMEWORK_MODULE_ID,
        version=FRAMEWORK_MODULE_VERSION,
        maturity="beta",
        manifest=_KERNEL_MANIFEST,
    ),
)

_CAPABILITIES: tuple[_CapabilityRow, ...] = (
    _CapabilityRow(
        capability_id="sample.notes.record",
        module_id=FRAMEWORK_MODULE_ID,
        contract_version="1.0.0",
        command_handler="northstar.sample.record",
        query_handler=None,
        policy_action="sample.notes.record",
    ),
    _CapabilityRow(
        capability_id="sample.notes.read",
        module_id=FRAMEWORK_MODULE_ID,
        contract_version="1.0.0",
        command_handler=None,
        query_handler="northstar.sample.read",
        policy_action="sample.notes.read",
    ),
)

_CONTRACTS: tuple[_ContractRow, ...] = (
    _ContractRow(
        contract_id="cli-output",
        version="1.0.0",
        kind="cli",
        schema_uri="https://schemas.northstar.example/cli-output/1.0.0",
        compatibility="backward",
    ),
)


@dataclass(frozen=True)
class SeedOutcome:
    """Structured result of a seed run: how many reference rows were inserted vs already present."""

    modules_inserted: int
    capabilities_inserted: int
    contracts_inserted: int
    modules_total: int
    capabilities_total: int
    contracts_total: int

    @property
    def inserted(self) -> int:
        return self.modules_inserted + self.capabilities_inserted + self.contracts_inserted

    @property
    def total(self) -> int:
        return self.modules_total + self.capabilities_total + self.contracts_total


# Statements target the fixed framework-registry schema created by migration 000001. The schema
# name is a trusted constant (``METADATA_SCHEMA``) written literally here — never a runtime value —
# so no user input is ever interpolated into SQL; all row data is passed as bound parameters.
_INSERT_MODULE = text(
    """
    INSERT INTO northstar_meta.module_registry
        (module_id, version, maturity, manifest, manifest_sha256, enabled)
    VALUES (:module_id, :version, :maturity, CAST(:manifest AS jsonb), :manifest_sha256, true)
    ON CONFLICT (module_id) DO NOTHING
    RETURNING module_id
    """
)

_INSERT_CAPABILITY = text(
    """
    INSERT INTO northstar_meta.capability_registry
        (capability_id, module_id, contract_version, command_handler, query_handler,
         policy_action, enabled)
    VALUES (:capability_id, :module_id, :contract_version, :command_handler, :query_handler,
            :policy_action, true)
    ON CONFLICT (capability_id) DO NOTHING
    RETURNING capability_id
    """
)

_INSERT_CONTRACT = text(
    """
    INSERT INTO northstar_meta.contract_registry
        (contract_id, version, kind, schema_uri, schema_sha256, compatibility)
    VALUES (:contract_id, :version, :kind, :schema_uri, :schema_sha256, :compatibility)
    ON CONFLICT (contract_id, version) DO NOTHING
    RETURNING contract_id
    """
)

_COUNT_MODULES = text("SELECT count(*) FROM northstar_meta.module_registry")
_COUNT_CAPABILITIES = text("SELECT count(*) FROM northstar_meta.capability_registry")
_COUNT_CONTRACTS = text("SELECT count(*) FROM northstar_meta.contract_registry")


def apply_seed(engine: Engine) -> SeedOutcome:
    """Insert the deterministic reference registry rows idempotently and report the outcome.

    Runs inside a single transaction. Because every insert is ``ON CONFLICT DO NOTHING``, calling
    this twice inserts the reference rows exactly once (no duplicates) — the second run reports
    ``*_inserted == 0`` while the totals stay stable.
    """
    modules_inserted = 0
    capabilities_inserted = 0
    contracts_inserted = 0
    with engine.begin() as conn:
        for module in _MODULES:
            row = conn.execute(
                _INSERT_MODULE,
                {
                    "module_id": module.module_id,
                    "version": module.version,
                    "maturity": module.maturity,
                    "manifest": json.dumps(module.manifest, sort_keys=True),
                    "manifest_sha256": _sha256_of(module.manifest),
                },
            ).first()
            modules_inserted += 1 if row is not None else 0
        for capability in _CAPABILITIES:
            row = conn.execute(
                _INSERT_CAPABILITY,
                {
                    "capability_id": capability.capability_id,
                    "module_id": capability.module_id,
                    "contract_version": capability.contract_version,
                    "command_handler": capability.command_handler,
                    "query_handler": capability.query_handler,
                    "policy_action": capability.policy_action,
                },
            ).first()
            capabilities_inserted += 1 if row is not None else 0
        for contract in _CONTRACTS:
            row = conn.execute(
                _INSERT_CONTRACT,
                {
                    "contract_id": contract.contract_id,
                    "version": contract.version,
                    "kind": contract.kind,
                    "schema_uri": contract.schema_uri,
                    "schema_sha256": _sha256_of(contract.schema_uri),
                    "compatibility": contract.compatibility,
                },
            ).first()
            contracts_inserted += 1 if row is not None else 0
        modules_total = int(conn.execute(_COUNT_MODULES).scalar_one())
        capabilities_total = int(conn.execute(_COUNT_CAPABILITIES).scalar_one())
        contracts_total = int(conn.execute(_COUNT_CONTRACTS).scalar_one())

    return SeedOutcome(
        modules_inserted=modules_inserted,
        capabilities_inserted=capabilities_inserted,
        contracts_inserted=contracts_inserted,
        modules_total=modules_total,
        capabilities_total=capabilities_total,
        contracts_total=contracts_total,
    )


__all__ = [
    "FRAMEWORK_MODULE_ID",
    "SeedOutcome",
    "apply_seed",
]
