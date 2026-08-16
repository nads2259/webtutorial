"""Typed media domain errors (rule 30/40): explainable, deterministic diagnostics.

The media domain raises these typed errors rather than bare strings; adapters map them to RFC 9457
problem details at the trust boundary. The :class:`AccessibilityRequirementNotMet` error is the
publish-time accessibility gate (EVAL-MED-002, NFR-A11Y-003): it is a hard, typed rejection, never
an advisory warning.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class MediaError(KernelError):
    """Base class for media domain errors."""


class MediaInvariantViolation(MediaError):  # noqa: N818 canonical error name
    """A media invariant was violated (e.g. bad media type, empty cue, wrong state)."""

    def __init__(self, message: str, code: str = "media.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class MediaNotFound(MediaInvariantViolation):
    """A media asset is absent in the caller's tenant scope (fail closed, do not disclose)."""

    def __init__(self) -> None:
        super().__init__("media asset is not available in this scope", code="media.not_found")


class TenantScopeMissing(MediaInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation", code="media.tenant.missing"
        )


class MediaStateError(MediaInvariantViolation):
    """An operation is invalid for the asset's current lifecycle state."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="media.state.invalid")


class AccessibilityRequirementNotMet(MediaInvariantViolation):
    """Publishing was refused because a required accessible alternative is missing (EVAL-MED-002).

    ``media_type`` is the offending asset kind and ``missing`` names the alternatives the media
    type requires but the asset lacks (``transcript``/``captions`` for time-based media, or
    ``alt_text`` for images). This is the hard accessibility gate (NFR-A11Y-003), not advisory.
    """

    def __init__(self, *, media_type: str, missing: tuple[str, ...]) -> None:
        self.media_type = media_type
        self.missing = missing
        joined = ", ".join(missing)
        super().__init__(
            f"a {media_type} asset cannot be published without: {joined}",
            code="media.accessibility.required",
        )
