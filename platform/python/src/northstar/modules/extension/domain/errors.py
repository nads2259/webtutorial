"""Typed, pure extension-domain errors (LAW-02, rule 30/40/50).

Deny-by-default, explainable refusals with machine-comparable ``code`` values. Adapters map these
to RFC 9457 problem details at the API edge; the domain stays infrastructure-free. Several of these
are the load-bearing supply-chain invariants of the module (rule 50, GATE-EXTENSION-GA): an
unsigned/forged/tampered/untrusted-publisher extension, an over-privileged (low-tier) capability
request, a disabled extension attempting to execute and a catalog listing without a verified
publisher all fail CLOSED with one of these typed refusals.
"""

from __future__ import annotations


class ExtensionError(Exception):
    """Base class for extension-domain errors (deny-by-default, explainable)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class ManifestInvalid(ExtensionError):  # noqa: N818 canonical error name
    """A manifest failed structural/schema validation (FR-EXT-001/002)."""

    def __init__(self, message: str, *, issues: tuple[str, ...] = ()) -> None:
        self.issues = issues
        super().__init__(message, code="extension.manifest.invalid")


class ExtensionUnsigned(ExtensionError):  # noqa: N818 canonical error name
    """An install/upgrade request carried no cryptographic signature (FR-EXT-004, fail closed)."""

    def __init__(self, detail: str = "extension artifact is unsigned") -> None:
        super().__init__(detail, code="extension.signature.unsigned")


class SignatureForged(ExtensionError):  # noqa: N818 canonical error name
    """A signature did not verify against the trusted publisher key (FR-EXT-004, EVAL-SEC-009)."""

    def __init__(self, detail: str = "extension signature is invalid") -> None:
        super().__init__(detail, code="extension.signature.forged")


class ArtifactTampered(ExtensionError):  # noqa: N818 canonical error name
    """The delivered artifact digest does not match the signed manifest digest (FR-EXT-004)."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            "extension artifact digest does not match the signed manifest digest",
            code="extension.artifact.tampered",
        )


class UntrustedPublisher(ExtensionError):  # noqa: N818 canonical error name
    """The signing publisher is unknown/unverified (FR-EXT-004/008, fail closed)."""

    def __init__(self, publisher_id: str) -> None:
        self.publisher_id = publisher_id
        super().__init__(
            f"publisher '{publisher_id}' is not a verified/trusted publisher",
            code="extension.publisher.untrusted",
        )


class PublisherNotVerified(ExtensionError):  # noqa: N818 canonical error name
    """A public catalog listing was refused because the publisher is not verified (FR-EXT-008)."""

    def __init__(self, publisher_id: str) -> None:
        self.publisher_id = publisher_id
        super().__init__(
            f"catalog listing requires a verified publisher; '{publisher_id}' is not verified",
            code="extension.catalog.publisher_unverified",
        )


class TrustTierViolation(ExtensionError):  # noqa: N818 canonical error name
    """A requested capability requires a higher trust tier than the extension was granted."""

    def __init__(self, action: str, granted: str, required: str) -> None:
        self.action = action
        self.granted = granted
        self.required = required
        super().__init__(
            f"permission '{action}' requires trust tier '{required}' but the extension was "
            f"granted only '{granted}'",
            code="extension.trust_tier.insufficient",
        )


class ExtensionDisabled(ExtensionError):  # noqa: N818 canonical error name
    """A disabled/uninstalled extension attempted to execute (FR-EXT-005, fail closed)."""

    def __init__(self, extension_id: str, state: str) -> None:
        self.extension_id = extension_id
        self.state = state
        super().__init__(
            f"extension '{extension_id}' is '{state}'; its capabilities are no longer dispatched",
            code="extension.lifecycle.stopped",
        )


class ExtensionNotFound(ExtensionError):  # noqa: N818 canonical error name
    """A referenced extension is absent in this tenant scope (fail closed)."""

    def __init__(self, extension_id: str) -> None:
        self.extension_id = extension_id
        super().__init__(
            f"extension '{extension_id}' is not installed in this scope",
            code="extension.not_found",
        )


class ExtensionAlreadyInstalled(ExtensionError):  # noqa: N818 canonical error name
    """An install targeted an extension that is already installed (use upgrade instead)."""

    def __init__(self, extension_id: str) -> None:
        self.extension_id = extension_id
        super().__init__(
            f"extension '{extension_id}' is already installed; upgrade instead",
            code="extension.already_installed",
        )


class BlockContentInvalid(ExtensionError):  # noqa: N818 canonical error name
    """Content-block content failed the extension-declared block schema (FR-EXT-007)."""

    def __init__(self, block_type: str, issues: tuple[str, ...] = ()) -> None:
        self.block_type = block_type
        self.issues = issues
        super().__init__(
            f"content for block '{block_type}' failed its declared schema",
            code="extension.block.invalid",
        )


class ThemeInvalid(ExtensionError):  # noqa: N818 canonical error name
    """A theme manifest/token set violated the theme invariants (FR-EXT-006)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="extension.theme.invalid")


class TenantScopeMissing(ExtensionError):  # noqa: N818 canonical error name
    """The authenticated request carried no tenant scope (rule 50, deny-by-default)."""

    def __init__(self) -> None:
        super().__init__(
            "tenant scope is required and must come from the authenticated context",
            code="extension.tenant.missing",
        )
