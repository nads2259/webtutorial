"""Ports (abstractions) for the extension application layer (rule 10/20, DIP).

Every infrastructure/supply-chain seam is a Protocol so the capabilities stay infrastructure-free
and hold no ambient authority (rule 50):

* :class:`SignatureVerifierPort` — verifies the cryptographic signature + provenance of an extension
  against a TRUSTED-publisher key registry and returns the assigned trust tier; an
  unsigned/forged/untrusted artifact fails closed (FR-EXT-004/008, EVAL-SEC-009).
* :class:`ManifestValidatorPort` — validates a manifest document against its canonical JSON Schema
  before the domain builds a value object (FR-EXT-001/002).
* :class:`ExtensionRegistryPort` — the module's own tenant-scoped persistence for installed
  extensions, catalog listings and applied themes (LAW-13); it is the source of truth for the
  enabled/disabled lifecycle state the dispatch guard honours (FR-EXT-005).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..domain.model import (
    CatalogListing,
    ExtensionInstallation,
    ExtensionManifest,
    LifecycleState,
    ThemeApplication,
    TrustTier,
)


class ManifestKind(StrEnum):
    """The canonical schema a document is validated against."""

    PLUGIN = "plugin-manifest"
    THEME = "theme-manifest"
    THEME_TOKEN = "theme-token"  # noqa: S105 schema name, not a secret
    CAPABILITY_CONTRACT = "capability-contract"


@dataclass(frozen=True, slots=True)
class TrustAssertion:
    """The result of a successful signature + provenance verification (FR-EXT-004).

    ``granted_trust_tier`` is assigned by the trusted-publisher record (review/deployment policy),
    NOT self-declared by the package; ``verified`` is always ``True`` when returned (a failure
    raises instead of returning an unverified assertion).
    """

    publisher_id: str
    granted_trust_tier: TrustTier
    verified: bool = True


@runtime_checkable
class SignatureVerifierPort(Protocol):
    """Verifies an extension's signature + provenance against trusted publishers (FR-EXT-004).

    Returns a :class:`TrustAssertion` for a correctly-signed, trusted, untampered artifact; raises
    :class:`~..domain.errors.UntrustedPublisher` for an unknown/unverified publisher and
    :class:`~..domain.errors.SignatureForged` for a signature that does not verify. The verifier
    holds NO application credential — only per-publisher verification keys.
    """

    def verify(self, manifest: ExtensionManifest) -> TrustAssertion: ...

    def is_verified_publisher(self, publisher_id: str) -> bool: ...


@runtime_checkable
class ManifestValidatorPort(Protocol):
    """Validates a manifest document against its canonical JSON Schema (FR-EXT-001/002)."""

    def validate(self, kind: ManifestKind, document: Mapping[str, object]) -> None: ...


@runtime_checkable
class ExtensionRegistryPort(Protocol):
    """Persists the extension aggregate; every method is tenant-scoped (rule 50, LAW-13)."""

    def add(self, installation: ExtensionInstallation) -> None: ...

    def get(self, *, organization_id: str, extension_id: str) -> ExtensionInstallation | None: ...

    def replace(self, installation: ExtensionInstallation) -> None: ...

    def set_state(
        self, *, organization_id: str, extension_id: str, state: LifecycleState
    ) -> None: ...

    def remove(self, *, organization_id: str, extension_id: str) -> None: ...

    def publish_listing(self, listing: CatalogListing) -> None: ...

    def get_listing(self, *, organization_id: str, extension_id: str) -> CatalogListing | None: ...

    def set_theme(self, application: ThemeApplication) -> None: ...

    def get_theme(self, *, organization_id: str, theme_id: str) -> ThemeApplication | None: ...
