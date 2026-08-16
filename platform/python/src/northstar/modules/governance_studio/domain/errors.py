"""Typed Governance Studio domain errors (stdlib-only, LAW-02).

The Studio is a control plane, not a data store: its domain errors describe contribution and
composition problems (an invalid contribution document, an incompatible ``studio_api`` version),
never persistence failures. They extend :class:`~northstar.kernel.errors.KernelError` so adapters
map them to RFC 9457 problem details at the trust boundary (rule 30/40).
"""

from __future__ import annotations

from northstar.kernel.errors import KernelError


class GovernanceStudioError(KernelError):
    """Base class for Governance Studio domain errors."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ContributionInvalid(GovernanceStudioError):  # noqa: N818 canonical error name
    """A module's Studio contribution document failed schema/semantic validation."""

    def __init__(self, message: str, *, issues: tuple[str, ...] = ()) -> None:
        super().__init__(message, code="governance_studio.contribution.invalid")
        self.issues = issues


class IncompatibleContribution(GovernanceStudioError):  # noqa: N818 canonical error name
    """A contribution declared a ``studio_api`` version the shell cannot host."""

    def __init__(self, module_id: str, studio_api: str, shell_api: str) -> None:
        super().__init__(
            f"module '{module_id}' targets studio_api {studio_api!r}, "
            f"incompatible with shell {shell_api!r}",
            code="governance_studio.contribution.incompatible",
        )
        self.module_id = module_id
        self.studio_api = studio_api
        self.shell_api = shell_api
