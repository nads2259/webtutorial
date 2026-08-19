"""Typed domain errors for the codelab module."""

from __future__ import annotations

from northstar.kernel.errors import KernelError


class CodelabError(KernelError):
    """Base class for codelab domain errors."""


class CodeInvalid(CodelabError):
    """The submitted code/request is structurally invalid (deny-by-default)."""


class TenantScopeMissing(CodelabError):
    """The authenticated context carries no tenant scope (rule 50)."""

    def __init__(self) -> None:
        super().__init__("a tenant scope is required")
