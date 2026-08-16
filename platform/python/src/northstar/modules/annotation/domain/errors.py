"""Typed annotation domain errors (rule 30/40): explainable, deterministic diagnostics.

The annotation domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class AnnotationError(KernelError):
    """Base class for annotation domain errors."""


class AnnotationInvariantViolation(AnnotationError):  # noqa: N818 canonical error name
    """An annotation invariant was violated (e.g. empty selector set, bad body, bad reply)."""

    def __init__(self, message: str, code: str = "annotation.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class InvalidSelector(AnnotationInvariantViolation):
    """A selector payload did not match any known selector shape (deny-by-default)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="annotation.selector.invalid")


class VisibilityBroadeningRejected(AnnotationInvariantViolation):
    """A reply attempted a broader visibility than its parent (rule 50: no disclosure leak)."""

    def __init__(self, reply: str, parent: str) -> None:
        super().__init__(
            f"a reply may not be more visible ({reply!r}) than its parent ({parent!r})",
            code="annotation.reply.visibility",
        )


class AnnotationNotFound(AnnotationInvariantViolation):
    """An annotation is absent in the caller's tenant scope (fail closed, do not disclose)."""

    def __init__(self) -> None:
        super().__init__("annotation is not available in this scope", code="annotation.not_found")


class TenantScopeMissing(AnnotationInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="annotation.tenant.missing",
        )
