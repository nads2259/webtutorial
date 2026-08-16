"""Typed retrieval domain errors (rule 30/40): explainable, deterministic diagnostics.

The retrieval domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class RetrievalError(KernelError):
    """Base class for retrieval domain errors."""


class RetrievalInvariantViolation(RetrievalError):  # noqa: N818 canonical error name
    """A retrieval invariant was violated (e.g. bad embedding profile, empty query)."""

    def __init__(self, message: str, code: str = "retrieval.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class TenantScopeMissing(RetrievalInvariantViolation):
    """A tenant-scoped retrieval op was invoked without an authenticated scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="retrieval.tenant.missing",
        )


class DimensionMismatch(RetrievalInvariantViolation):
    """A vector's dimensionality does not match the active embedding profile."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"embedding vector has {actual} dimensions; profile requires {expected}",
            code="retrieval.embedding.dimension",
        )
        self.expected = expected
        self.actual = actual
