"""Typed knowledge domain errors (rule 30/40): explainable, deterministic diagnostics.

The knowledge domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class KnowledgeError(KernelError):
    """Base class for knowledge domain errors."""


class KnowledgeInvariantViolation(KnowledgeError):  # noqa: N818 canonical error name
    """A document/block invariant was violated (e.g. empty title, bad block payload)."""

    def __init__(self, message: str, code: str = "knowledge.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class UnknownBlockType(KnowledgeError):  # noqa: N818 canonical error name
    """A block declared a type that is not in the block-type registry (deny-by-default)."""

    def __init__(self, block_type: str) -> None:
        message = f"block type {block_type!r} is not a registered knowledge block type"
        super().__init__(
            message, (Diagnostic(code="knowledge.block.unknown-type", message=message),)
        )
        self.block_type = block_type


class ImmutableRevisionError(KnowledgeError):
    """An attempt was made to mutate an already-published, immutable revision (LAW-07)."""

    def __init__(self, revision_id: str) -> None:
        message = (
            f"revision {revision_id!r} is published and immutable; "
            "corrections must create a new revision"
        )
        super().__init__(
            message, (Diagnostic(code="knowledge.revision.immutable", message=message),)
        )
        self.revision_id = revision_id


class TenantScopeMissing(KnowledgeInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="knowledge.tenant.missing",
        )
