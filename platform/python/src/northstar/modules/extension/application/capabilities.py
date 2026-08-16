"""Extension capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command bus, so each mutation is authorized deny-by-default
and audited (rule 50, LAW-14). Tenant scope + acting subject come from the authenticated
:class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on :mod:`.ports` and
the pure :mod:`..domain`.

The supply-chain invariants are enforced here by construction and are never weakened:

* ``extension.install`` and ``extension.upgrade`` verify a cryptographic signature + provenance and
  REJECT (fail closed) an unsigned / forged-signature / tampered-artifact / untrusted-publisher
  extension BEFORE activation; the granted trust tier then gates which requested capabilities the
  extension may hold (FR-EXT-003/004, EVAL-SEC-009).
* ``extension.disable`` / ``extension.uninstall`` stop execution and revoke grants — a disabled or
  uninstalled extension's capabilities are no longer dispatched (FR-EXT-005).
* ``theme.apply`` changes ONLY semantic tokens + declared presentation slots and never a policy
  decision (FR-EXT-006).
* ``catalog.publish`` refuses a listing unless the publisher is verified (FR-EXT-008).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..domain.errors import (
    ExtensionAlreadyInstalled,
    ExtensionNotFound,
    ExtensionUnsigned,
    ManifestInvalid,
    PublisherNotVerified,
    TenantScopeMissing,
)
from ..domain.model import (
    ContentBlockExtension,
    ExtensionInstallation,
    ExtensionType,
    LifecycleState,
    ThemeApplication,
    apply_theme,
    digest_of,
    ensure_trust_tier_permits,
    manifest_from_document,
    theme_manifest_from_document,
    theme_token_from_document,
)
from .ports import (
    ExtensionRegistryPort,
    ManifestKind,
    ManifestValidatorPort,
    SignatureVerifierPort,
)

CAP_VERSION = "1.0.0"

CAP_INSTALL = "extension.install"
CAP_UPGRADE = "extension.upgrade"
CAP_DISABLE = "extension.disable"
CAP_UNINSTALL = "extension.uninstall"
CAP_APPLY_THEME = "theme.apply"
CAP_PUBLISH_CATALOG = "catalog.publish"

EXTENSION_CAPABILITIES: tuple[str, ...] = (
    CAP_INSTALL,
    CAP_UPGRADE,
    CAP_DISABLE,
    CAP_UNINSTALL,
    CAP_APPLY_THEME,
    CAP_PUBLISH_CATALOG,
)

RES_EXTENSION = "extension.extension"


# ---------------------------------------------------------------------------
# Command payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstallExtensionCommand:
    manifest: Mapping[str, object]
    artifact: bytes = b""
    block_schema: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class InstallExtensionResult:
    extension_id: str
    version: str
    granted_trust_tier: str
    state: str


@dataclass(frozen=True, slots=True)
class UpgradeExtensionCommand:
    manifest: Mapping[str, object]
    artifact: bytes = b""


@dataclass(frozen=True, slots=True)
class UpgradeExtensionResult:
    extension_id: str
    version: str
    granted_trust_tier: str
    state: str


@dataclass(frozen=True, slots=True)
class DisableExtensionCommand:
    extension_id: str


@dataclass(frozen=True, slots=True)
class DisableExtensionResult:
    extension_id: str
    state: str
    revoked_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UninstallExtensionCommand:
    extension_id: str


@dataclass(frozen=True, slots=True)
class UninstallExtensionResult:
    extension_id: str
    data_policy: str
    revoked_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplyThemeCommand:
    theme_manifest: Mapping[str, object]
    theme_tokens: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ApplyThemeResult:
    theme_id: str
    version: str
    slots: tuple[str, ...]
    modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishCatalogCommand:
    manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PublishCatalogResult:
    extension_id: str
    version: str
    publisher_id: str
    verified: bool


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


# ---------------------------------------------------------------------------
# Shared verification (install AND upgrade run the identical checks — FR-EXT-004)
# ---------------------------------------------------------------------------


def _verify_and_build(
    *,
    document: Mapping[str, object],
    artifact: bytes,
    validator: ManifestValidatorPort,
    verifier: SignatureVerifierPort,
) -> tuple[object, object]:
    """Validate, verify signature+provenance+integrity and gate the trust tier (fail closed).

    Order (each raises + activates nothing on failure): unsigned → schema → structural → tamper →
    signature/provenance/publisher → trust-tier gate. Returns ``(manifest, trust_assertion)``.
    """
    artifacts = dict(document.get("artifacts", {}) or {})  # type: ignore[arg-type]

    # 1. Unsigned artifacts are refused before anything else (fail closed).
    signature = str(artifacts.get("signature", "") or "").strip()
    if not signature:
        raise ExtensionUnsigned()
    if not str(artifacts.get("provenance", "") or "").strip():
        raise ExtensionUnsigned("extension artifact declares no provenance")

    # 2. Full JSON-Schema validation against the canonical plugin-manifest contract.
    validator.validate(ManifestKind.PLUGIN, document)

    # 3. Structural domain value object.
    manifest = manifest_from_document(document)

    # 4. Integrity: the delivered artifact bytes must hash to the SIGNED digest (tamper check).
    actual_digest = digest_of(artifact)
    if actual_digest != manifest.artifacts.package_digest:
        from ..domain.errors import ArtifactTampered

        raise ArtifactTampered(manifest.artifacts.package_digest, actual_digest)

    # 5. Signature + provenance against the trusted-publisher key registry (assigns the tier).
    assertion = verifier.verify(manifest)

    # 6. Trust-tier gate: a low-tier extension cannot hold a high-risk requested capability.
    ensure_trust_tier_permits(assertion.granted_trust_tier, manifest.permissions)

    return manifest, assertion


def _build_content_block(
    manifest: object, block_schema: Mapping[str, object] | None
) -> ContentBlockExtension | None:
    """For content_block extensions, build + validate the extension-declared block schema."""
    if manifest.extension_type is not ExtensionType.CONTENT_BLOCK:  # type: ignore[attr-defined]
        return None
    if block_schema is None:
        raise ManifestInvalid("a content_block extension must declare its own block schema")
    return ContentBlockExtension(
        block_type=manifest.plugin_id,  # type: ignore[attr-defined]
        schema_version=manifest.version,  # type: ignore[attr-defined]
        block_schema=block_schema,
    )


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class InstallExtension:
    """``extension.install`` — verify signature+provenance, gate trust tier, then activate."""

    def __init__(
        self,
        *,
        registry: ExtensionRegistryPort,
        verifier: SignatureVerifierPort,
        validator: ManifestValidatorPort,
    ) -> None:
        self._registry = registry
        self._verifier = verifier
        self._validator = validator

    def handle(self, request: object) -> InstallExtensionResult:
        command = _typed(request, InstallExtensionCommand)
        organization_id = _tenant(request)
        manifest, assertion = _verify_and_build(
            document=command.manifest,
            artifact=command.artifact,
            validator=self._validator,
            verifier=self._verifier,
        )
        _build_content_block(manifest, command.block_schema)

        existing = self._registry.get(
            organization_id=organization_id, extension_id=manifest.plugin_id
        )
        if existing is not None:
            raise ExtensionAlreadyInstalled(manifest.plugin_id)

        installation = ExtensionInstallation(
            organization_id=organization_id,
            extension_id=manifest.plugin_id,
            version=manifest.version,
            publisher_id=manifest.publisher_id,
            extension_type=manifest.extension_type,
            required_trust_tier=manifest.required_trust_tier,
            granted_trust_tier=assertion.granted_trust_tier,
            permissions=manifest.permissions,
            package_digest=manifest.artifacts.package_digest,
            uninstall_policy=manifest.uninstall_policy,
            state=LifecycleState.ENABLED,
        )
        self._registry.add(installation)
        return InstallExtensionResult(
            extension_id=installation.extension_id,
            version=installation.version,
            granted_trust_tier=installation.granted_trust_tier.value,
            state=installation.state.value,
        )


class UpgradeExtension:
    """``extension.upgrade`` — re-verify signature+provenance for the NEW version (FR-EXT-004)."""

    def __init__(
        self,
        *,
        registry: ExtensionRegistryPort,
        verifier: SignatureVerifierPort,
        validator: ManifestValidatorPort,
    ) -> None:
        self._registry = registry
        self._verifier = verifier
        self._validator = validator

    def handle(self, request: object) -> UpgradeExtensionResult:
        command = _typed(request, UpgradeExtensionCommand)
        organization_id = _tenant(request)
        manifest, assertion = _verify_and_build(
            document=command.manifest,
            artifact=command.artifact,
            validator=self._validator,
            verifier=self._verifier,
        )
        existing = self._registry.get(
            organization_id=organization_id, extension_id=manifest.plugin_id
        )
        if existing is None:
            raise ExtensionNotFound(manifest.plugin_id)

        upgraded = replace(
            existing,
            version=manifest.version,
            publisher_id=manifest.publisher_id,
            extension_type=manifest.extension_type,
            required_trust_tier=manifest.required_trust_tier,
            granted_trust_tier=assertion.granted_trust_tier,
            permissions=manifest.permissions,
            package_digest=manifest.artifacts.package_digest,
            uninstall_policy=manifest.uninstall_policy,
            state=LifecycleState.ENABLED,
        )
        self._registry.replace(upgraded)
        return UpgradeExtensionResult(
            extension_id=upgraded.extension_id,
            version=upgraded.version,
            granted_trust_tier=upgraded.granted_trust_tier.value,
            state=upgraded.state.value,
        )


class DisableExtension:
    """``extension.disable`` — stop execution + revoke grants (FR-EXT-005)."""

    def __init__(self, *, registry: ExtensionRegistryPort) -> None:
        self._registry = registry

    def handle(self, request: object) -> DisableExtensionResult:
        command = _typed(request, DisableExtensionCommand)
        organization_id = _tenant(request)
        existing = self._registry.get(
            organization_id=organization_id, extension_id=command.extension_id
        )
        if existing is None:
            raise ExtensionNotFound(command.extension_id)
        self._registry.set_state(
            organization_id=organization_id,
            extension_id=command.extension_id,
            state=LifecycleState.DISABLED,
        )
        return DisableExtensionResult(
            extension_id=command.extension_id,
            state=LifecycleState.DISABLED.value,
            revoked_actions=existing.granted_actions,
        )


class UninstallExtension:
    """``extension.uninstall`` — remove, revoke grants and honour the declared data policy."""

    def __init__(self, *, registry: ExtensionRegistryPort) -> None:
        self._registry = registry

    def handle(self, request: object) -> UninstallExtensionResult:
        command = _typed(request, UninstallExtensionCommand)
        organization_id = _tenant(request)
        existing = self._registry.get(
            organization_id=organization_id, extension_id=command.extension_id
        )
        if existing is None:
            raise ExtensionNotFound(command.extension_id)
        self._registry.remove(organization_id=organization_id, extension_id=command.extension_id)
        return UninstallExtensionResult(
            extension_id=command.extension_id,
            data_policy=existing.uninstall_policy.value,
            revoked_actions=existing.granted_actions,
        )


class ApplyTheme:
    """``theme.apply`` — apply semantic tokens + declared slots ONLY; never authorization.

    Applying a theme records a presentation projection and touches NO policy grant, so a
    capability/permission decision is identical with or without the theme (FR-EXT-006).
    """

    def __init__(
        self, *, registry: ExtensionRegistryPort, validator: ManifestValidatorPort
    ) -> None:
        self._registry = registry
        self._validator = validator

    def handle(self, request: object) -> ApplyThemeResult:
        command = _typed(request, ApplyThemeCommand)
        organization_id = _tenant(request)
        self._validator.validate(ManifestKind.THEME, command.theme_manifest)
        self._validator.validate(ManifestKind.THEME_TOKEN, command.theme_tokens)
        manifest = theme_manifest_from_document(command.theme_manifest)
        token = theme_token_from_document(command.theme_tokens)
        presentation = apply_theme(manifest, token)
        self._registry.set_theme(
            ThemeApplication(
                organization_id=organization_id,
                theme_id=manifest.theme_id,
                version=token.version,
                presentation=presentation,
            )
        )
        return ApplyThemeResult(
            theme_id=presentation.theme_id,
            version=presentation.version,
            slots=presentation.slots,
            modes=presentation.modes,
        )


class PublishCatalog:
    """``catalog.publish`` — a public catalog listing REQUIRES a verified publisher (FR-EXT-008)."""

    def __init__(
        self,
        *,
        registry: ExtensionRegistryPort,
        verifier: SignatureVerifierPort,
        validator: ManifestValidatorPort,
    ) -> None:
        self._registry = registry
        self._verifier = verifier
        self._validator = validator

    def handle(self, request: object) -> PublishCatalogResult:
        command = _typed(request, PublishCatalogCommand)
        organization_id = _tenant(request)
        self._validator.validate(ManifestKind.PLUGIN, command.manifest)
        manifest = manifest_from_document(command.manifest)
        # Deny-by-default: only a verified publisher with a valid signature may be listed.
        if not self._verifier.is_verified_publisher(manifest.publisher_id):
            raise PublisherNotVerified(manifest.publisher_id)
        self._verifier.verify(manifest)
        from ..domain.model import CatalogListing

        listing = CatalogListing(
            organization_id=organization_id,
            extension_id=manifest.plugin_id,
            version=manifest.version,
            publisher_id=manifest.publisher_id,
            verified=True,
            permissions=manifest.permissions,
        )
        self._registry.publish_listing(listing)
        return PublishCatalogResult(
            extension_id=listing.extension_id,
            version=listing.version,
            publisher_id=listing.publisher_id,
            verified=listing.verified,
        )


__all__ = [
    "CAP_APPLY_THEME",
    "CAP_DISABLE",
    "CAP_INSTALL",
    "CAP_PUBLISH_CATALOG",
    "CAP_UNINSTALL",
    "CAP_UPGRADE",
    "CAP_VERSION",
    "EXTENSION_CAPABILITIES",
    "RES_EXTENSION",
    "ApplyTheme",
    "ApplyThemeCommand",
    "ApplyThemeResult",
    "DisableExtension",
    "DisableExtensionCommand",
    "DisableExtensionResult",
    "InstallExtension",
    "InstallExtensionCommand",
    "InstallExtensionResult",
    "PublishCatalog",
    "PublishCatalogCommand",
    "PublishCatalogResult",
    "UninstallExtension",
    "UninstallExtensionCommand",
    "UninstallExtensionResult",
    "UpgradeExtension",
    "UpgradeExtensionCommand",
    "UpgradeExtensionResult",
]
