"""Extension object model (docs/14, FR-EXT-001..008). Pure and infrastructure-free (rule 10).

Every value object here is a frozen dataclass with its invariants enforced on construction; no
database, network or provider SDK is reachable. The security-critical pieces live here so they are
provable in isolation:

* :class:`ExtensionManifest` / :class:`ThemeManifest` / :class:`ThemeToken` /
  :class:`CapabilityContract` are schema-shaped, IMMUTABLE value objects (FR-EXT-001/002). Full
  JSON-Schema conformance is asserted by the contract tests; the domain enforces the structural
  invariants + supplies the canonical signing material.
* :class:`TrustTier` + :func:`ensure_trust_tier_permits` model the rule that a higher-risk
  requested capability requires a higher trust tier; a low-tier extension requesting a high-risk
  permission is refused (FR-EXT-003).
* :func:`ExtensionManifest.signing_material` is the canonical claim set an adapter signs/verifies
  for install AND upgrade; tampering with the digest, provenance or SBOM breaks the signature
  (FR-EXT-004).
* :class:`ContentBlockExtension` defines + validates its own block schema; malformed block content
  is rejected (FR-EXT-007).
* :class:`ThemeApplication` / :func:`apply_theme` change ONLY semantic tokens + declared
  presentation slots and carry no permission surface, so applying a theme can never alter an
  authorization decision (FR-EXT-006).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import jsonschema

from .errors import (
    BlockContentInvalid,
    ManifestInvalid,
    ThemeInvalid,
    TrustTierViolation,
)


def _canonical(value: object) -> str:
    """Deterministic canonical JSON (sorted keys, no whitespace) for hashing/signing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(payload: bytes) -> str:
    """The ``sha256:<hex>`` digest of an artifact's bytes (used for the tamper check)."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Trust tiers (FR-EXT-003, docs/14 §3) — T0 first-party (highest privilege) .. T3 data-only
# ---------------------------------------------------------------------------


class TrustTier(StrEnum):
    """Extension trust tiers (``plugin-manifest`` ``required_trust_tier``)."""

    T0 = "T0"  # first-party release — in-process permitted
    T1 = "T1"  # approved partner — scoped APIs, signed migrations
    T2 = "T2"  # third-party restricted — brokered, no direct DB/secrets
    T3 = "T3"  # declarative/untrusted — data-only (themes, schemas)


# Privilege ranking: a higher rank grants more power (T0 strongest, T3 weakest).
_TIER_RANK: dict[TrustTier, int] = {
    TrustTier.T3: 0,
    TrustTier.T2: 1,
    TrustTier.T1: 2,
    TrustTier.T0: 3,
}
_RANK_TIER: dict[int, TrustTier] = {rank: tier for tier, rank in _TIER_RANK.items()}

# A permission's required minimum privilege rank derived from its resource scope. Broad scopes
# ("global") are prohibited for T2/T3 and only reachable by T0/T1 (docs/14 §5).
_SCOPE_MIN_RANK: dict[str, int] = {
    "global": 2,  # >= T1
    "product": 1,  # >= T2
    "organization": 1,  # >= T2
    "workspace": 0,
    "owned": 0,
    "explicit": 0,
}
# A permission touching more sensitive data classifications requires a higher tier as well.
_CLASSIFICATION_MIN_RANK: dict[str, int] = {
    "restricted": 2,  # >= T1
    "confidential": 1,  # >= T2
    "internal": 0,
    "public": 0,
}


def tier_rank(tier: TrustTier) -> int:
    return _TIER_RANK[tier]


def _rank_to_tier(rank: int) -> TrustTier:
    return _RANK_TIER[max(0, min(3, rank))]


def required_rank_for_permission(permission: Permission) -> int:
    """The minimum trust-tier privilege rank a permission requires (deny-by-default risk model)."""
    ranks = [_SCOPE_MIN_RANK.get(permission.resource_scope or "", 0)]
    ranks.extend(_CLASSIFICATION_MIN_RANK.get(c, 0) for c in permission.data_classifications)
    return max(ranks) if ranks else 0


def required_tier_for_permission(permission: Permission) -> TrustTier:
    return _rank_to_tier(required_rank_for_permission(permission))


def ensure_trust_tier_permits(granted: TrustTier, permissions: Sequence[Permission]) -> None:
    """Refuse if any requested permission needs a higher tier than ``granted`` (FR-EXT-003).

    Raises :class:`TrustTierViolation` for the first over-privileged permission so a low-tier
    extension can never hold a high-risk capability.
    """
    granted_rank = tier_rank(granted)
    for permission in permissions:
        required = required_rank_for_permission(permission)
        if granted_rank < required:
            raise TrustTierViolation(
                permission.action, granted.value, _rank_to_tier(required).value
            )


# ---------------------------------------------------------------------------
# Permission + identity value objects (FR-EXT-002)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Permission:
    """A single granular permission an extension requests (``plugin-manifest`` ``permissions``)."""

    action: str
    resource_scope: str | None = None
    data_classifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.action:
            raise ManifestInvalid("permission.action is required")

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"action": self.action}
        if self.resource_scope is not None:
            data["resource_scope"] = self.resource_scope
        if self.data_classifications:
            data["data_classifications"] = list(self.data_classifications)
        return data


class ExtensionType(StrEnum):
    """Extension types (``plugin-manifest`` ``extension_type``)."""

    PROVIDER = "provider"
    CONTENT_BLOCK = "content_block"
    STUDIO = "studio"
    INTEGRATION = "integration"
    WORKFLOW = "workflow"
    POLICY_PACKAGE = "policy_package"
    SIMULATION_RUNTIME = "simulation_runtime"
    AI_TOOL = "ai_tool"
    COMPOSITE = "composite"


class UninstallDataPolicy(StrEnum):
    """The declared data disposition on uninstall (``plugin-manifest`` ``lifecycle.uninstall``)."""

    RETAIN = "retain"
    EXPORT_THEN_DELETE = "export_then_delete"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """The signed artifact reference (``plugin-manifest`` ``artifacts``)."""

    package_digest: str
    signature: str
    sbom: str
    provenance: str

    def to_dict(self) -> dict[str, str]:
        return {
            "package_digest": self.package_digest,
            "signature": self.signature,
            "sbom": self.sbom,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    """A schema-shaped, IMMUTABLE extension/plugin manifest (FR-EXT-001/002).

    The dataclass is frozen; a change is a new version. :meth:`signing_material` is the canonical
    claim set the signature covers, binding the digest, provenance and SBOM to the identity so a
    tampered artifact or forged provenance cannot pass verification (FR-EXT-004).
    """

    plugin_id: str
    version: str
    publisher_id: str
    publisher_name: str
    extension_type: ExtensionType
    required_trust_tier: TrustTier
    framework: str
    permissions: tuple[Permission, ...]
    artifacts: ArtifactRef
    uninstall_policy: UninstallDataPolicy
    name: str | None = None
    manifest_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.plugin_id:
            raise ManifestInvalid("plugin_id is required")
        if not self.publisher_id:
            raise ManifestInvalid("publisher.id is required")
        if not self.version:
            raise ManifestInvalid("version is required")

    def signing_material(self) -> dict[str, object]:
        """The canonical claim set an adapter signs/verifies (order-independent, FR-EXT-004)."""
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "publisher_id": self.publisher_id,
            "package_digest": self.artifacts.package_digest,
            "provenance": self.artifacts.provenance,
            "sbom": self.artifacts.sbom,
        }


def signing_payload(manifest: ExtensionManifest) -> bytes:
    """The canonical bytes to HMAC/sign for ``manifest`` (deterministic, order-independent)."""
    return _canonical(manifest.signing_material()).encode("utf-8")


def manifest_from_document(document: Mapping[str, object]) -> ExtensionManifest:
    """Build an :class:`ExtensionManifest` from a schema-shaped document (structural validation).

    Raises :class:`ManifestInvalid` for a structurally invalid document. Full JSON-Schema
    conformance is asserted by the contract test; this keeps the domain infrastructure-free.
    """
    try:
        publisher = dict(document["publisher"])  # type: ignore[arg-type]
        compatibility = dict(document["compatibility"])  # type: ignore[arg-type]
        artifacts_raw = dict(document["artifacts"])  # type: ignore[arg-type]
        lifecycle_raw = dict(document["lifecycle"])  # type: ignore[arg-type]
        uninstall_raw = dict(lifecycle_raw["uninstall"])  # type: ignore[arg-type]
        permissions = tuple(
            Permission(
                action=str(entry["action"]),
                resource_scope=(
                    str(entry["resource_scope"]) if entry.get("resource_scope") else None
                ),
                data_classifications=tuple(str(c) for c in entry.get("data_classifications", ())),
            )
            for entry in document.get("permissions", ())  # type: ignore[union-attr]
        )
        return ExtensionManifest(
            plugin_id=str(document["plugin_id"]),
            version=str(document["version"]),
            publisher_id=str(publisher["id"]),
            publisher_name=str(publisher["name"]),
            extension_type=ExtensionType(str(document["extension_type"])),
            required_trust_tier=TrustTier(str(document["required_trust_tier"])),
            framework=str(compatibility["framework"]),
            permissions=permissions,
            artifacts=ArtifactRef(
                package_digest=str(artifacts_raw["package_digest"]),
                signature=str(artifacts_raw["signature"]),
                sbom=str(artifacts_raw["sbom"]),
                provenance=str(artifacts_raw["provenance"]),
            ),
            uninstall_policy=UninstallDataPolicy(str(uninstall_raw["data_policy"])),
            name=str(document["name"]) if document.get("name") is not None else None,
            manifest_version=str(document.get("manifest_version", "1.0")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestInvalid(f"extension manifest is malformed: {exc}") from exc


# ---------------------------------------------------------------------------
# Capability contract (FR-EXT-001) — a stable extension API surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    """A schema-shaped, IMMUTABLE capability contract (``capability-contract.schema.json``)."""

    capability_id: str
    contract_version: str
    owner_module: str
    kind: str
    input_schema: str
    output_schema: str
    action: str
    resource_type: str

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ManifestInvalid("capability_id is required")


def capability_contract_from_document(document: Mapping[str, object]) -> CapabilityContract:
    """Build a :class:`CapabilityContract` from a schema-shaped document (structural validation)."""
    try:
        policy = dict(document["policy"])  # type: ignore[arg-type]
        return CapabilityContract(
            capability_id=str(document["capability_id"]),
            contract_version=str(document["contract_version"]),
            owner_module=str(document["owner_module"]),
            kind=str(document["kind"]),
            input_schema=str(document["input_schema"]),
            output_schema=str(document["output_schema"]),
            action=str(policy["action"]),
            resource_type=str(policy["resource_type"]),
        )
    except (KeyError, TypeError) as exc:
        raise ManifestInvalid(f"capability contract is malformed: {exc}") from exc


# ---------------------------------------------------------------------------
# Themes (FR-EXT-006) — semantic tokens + presentation slots ONLY, no authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThemeToken:
    """A schema-shaped semantic token set (``theme-token.schema.json``, FR-EXT-006).

    A token set carries ONLY presentation tokens (color/typography/space/radius/motion) and
    accessibility flags. It has NO permission/authorization surface by construction, so applying it
    can never change a policy decision.
    """

    theme_id: str
    version: str
    framework_compatibility: str
    tokens: Mapping[str, Mapping[str, object]]
    accessibility_target: str
    supports_reduced_motion: bool
    supports_forced_colors: bool

    def __post_init__(self) -> None:
        required = ("color", "typography", "space", "radius", "motion")
        missing = [group for group in required if group not in self.tokens]
        if missing:
            raise ThemeInvalid(f"theme tokens missing required groups: {missing}")


def theme_token_from_document(document: Mapping[str, object]) -> ThemeToken:
    """Build a :class:`ThemeToken` from a schema-shaped document (structural validation)."""
    try:
        tokens_raw = dict(document["tokens"])  # type: ignore[arg-type]
        accessibility = dict(document["accessibility"])  # type: ignore[arg-type]
        return ThemeToken(
            theme_id=str(document["theme_id"]),
            version=str(document["version"]),
            framework_compatibility=str(document["framework_compatibility"]),
            tokens={str(k): dict(v) for k, v in tokens_raw.items()},  # type: ignore[arg-type]
            accessibility_target=str(accessibility["target"]),
            supports_reduced_motion=bool(accessibility["supports_reduced_motion"]),
            supports_forced_colors=bool(accessibility["supports_forced_colors"]),
        )
    except (KeyError, TypeError) as exc:
        raise ThemeInvalid(f"theme token document is malformed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ThemeManifest:
    """A schema-shaped, IMMUTABLE theme manifest (``theme-manifest.schema.json``, FR-EXT-006)."""

    theme_id: str
    version: str
    design_system: str
    modes: tuple[str, ...]
    presentation_slots: tuple[str, ...] = ()
    supports_rtl: bool = False

    def __post_init__(self) -> None:
        if not self.theme_id:
            raise ThemeInvalid("theme_id is required")
        if not self.modes:
            raise ThemeInvalid("a theme must declare at least one mode")


def theme_manifest_from_document(document: Mapping[str, object]) -> ThemeManifest:
    """Build a :class:`ThemeManifest` from a schema-shaped document (structural validation)."""
    try:
        compatibility = dict(document["compatibility"])  # type: ignore[arg-type]
        slots = tuple(str(a["slot"]) for a in document.get("assets", ()))  # type: ignore[union-attr]
        return ThemeManifest(
            theme_id=str(document["theme_id"]),
            version=str(document["version"]),
            design_system=str(compatibility["design_system"]),
            modes=tuple(str(m) for m in document["modes"]),  # type: ignore[union-attr]
            presentation_slots=slots,
            supports_rtl=bool(document.get("supports_rtl", False)),
        )
    except (KeyError, TypeError) as exc:
        raise ThemeInvalid(f"theme manifest is malformed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Presentation:
    """The presentation projection a theme yields: semantic tokens + declared slots ONLY.

    There is no permission/policy field here by design — a theme is presentation, never
    authorization (FR-EXT-006).
    """

    theme_id: str
    version: str
    tokens: Mapping[str, Mapping[str, object]]
    slots: tuple[str, ...]
    modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ThemeApplication:
    """The record of a theme applied to a tenant (semantic presentation only, FR-EXT-006)."""

    organization_id: str
    theme_id: str
    version: str
    presentation: Presentation


def apply_theme(manifest: ThemeManifest, token: ThemeToken) -> Presentation:
    """Project a theme to its presentation (tokens + declared slots) — never authorization.

    Refuses a theme/token mismatch (deny-by-default). The result carries NO permission surface, so a
    policy/capability decision is identical with or without the applied theme (FR-EXT-006).
    """
    if manifest.theme_id != token.theme_id:
        raise ThemeInvalid(
            f"theme manifest '{manifest.theme_id}' does not match token set '{token.theme_id}'"
        )
    return Presentation(
        theme_id=manifest.theme_id,
        version=token.version,
        tokens=token.tokens,
        slots=manifest.presentation_slots,
        modes=manifest.modes,
    )


# ---------------------------------------------------------------------------
# Content-block extensions (FR-EXT-007) — the extension defines + validates its own schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContentBlockExtension:
    """A content-block extension that defines + validates its own block schema (FR-EXT-007).

    The extension supplies its own JSON Schema (``block_schema``); :meth:`validate` rejects
    malformed block content deny-by-default. The schema itself must be a valid Draft 2020-12 schema.
    """

    block_type: str
    schema_version: str
    block_schema: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.block_type:
            raise ManifestInvalid("content block block_type is required")
        try:
            jsonschema.Draft202012Validator.check_schema(dict(self.block_schema))
        except jsonschema.exceptions.SchemaError as exc:
            raise ManifestInvalid(
                f"content block '{self.block_type}' declares an invalid schema: {exc.message}"
            ) from exc

    def validate(self, content: Mapping[str, object]) -> None:
        """Validate block ``content`` against the declared schema; malformed content is rejected."""
        validator = jsonschema.Draft202012Validator(dict(self.block_schema))
        errors = sorted(validator.iter_errors(dict(content)), key=lambda e: list(e.absolute_path))
        if errors:
            issues = tuple(
                f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
                for err in errors
            )
            raise BlockContentInvalid(self.block_type, issues=issues)


# ---------------------------------------------------------------------------
# Installed-extension lifecycle (FR-EXT-005) + catalog listing (FR-EXT-008)
# ---------------------------------------------------------------------------


class LifecycleState(StrEnum):
    """The lifecycle state of an installed extension (FR-EXT-005)."""

    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ExtensionInstallation:
    """An installed extension record scoped to a tenant (FR-EXT-004/005).

    ``granted_trust_tier`` is assigned by review/deployment policy (never self-declared) and gates
    the permissions the extension may hold. ``permissions`` is the granted set the dispatch guard
    honours; when the extension is disabled/uninstalled these grants no longer dispatch.
    """

    organization_id: str
    extension_id: str
    version: str
    publisher_id: str
    extension_type: ExtensionType
    required_trust_tier: TrustTier
    granted_trust_tier: TrustTier
    permissions: tuple[Permission, ...]
    package_digest: str
    uninstall_policy: UninstallDataPolicy
    state: LifecycleState = LifecycleState.ENABLED

    def __post_init__(self) -> None:
        if not self.organization_id:
            raise ManifestInvalid("organization_id is required")

    @property
    def granted_actions(self) -> tuple[str, ...]:
        """The capability/permission actions this extension is granted while enabled."""
        return tuple(permission.action for permission in self.permissions)


@dataclass(frozen=True, slots=True)
class CatalogListing:
    """A public catalog listing that requires a verified publisher (FR-EXT-008)."""

    organization_id: str
    extension_id: str
    version: str
    publisher_id: str
    verified: bool = True
    permissions: tuple[Permission, ...] = field(default_factory=tuple)


__all__ = [
    "ArtifactRef",
    "CapabilityContract",
    "CatalogListing",
    "ContentBlockExtension",
    "ExtensionInstallation",
    "ExtensionManifest",
    "ExtensionType",
    "LifecycleState",
    "Permission",
    "Presentation",
    "ThemeApplication",
    "ThemeManifest",
    "ThemeToken",
    "TrustTier",
    "UninstallDataPolicy",
    "apply_theme",
    "capability_contract_from_document",
    "digest_of",
    "ensure_trust_tier_permits",
    "manifest_from_document",
    "required_rank_for_permission",
    "required_tier_for_permission",
    "signing_payload",
    "theme_manifest_from_document",
    "theme_token_from_document",
    "tier_rank",
]
