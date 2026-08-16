"""Organization domain model: tenancy tree, memberships, roles and invariants (docs/07 §5, §9).

Pure, infrastructure-free (rule 10, LAW-02). The organization is the tenant root; workspaces and
teams nest beneath it and always carry the owning ``organization_id`` (docs/07 §9 — every
tenant-scoped record has an explicit scope). Memberships bind a subject to a scope with a role;
roles are permission *bundles* (docs/07 §5), and custom/assigned role sets are validated against
separation-of-duty rules. Cross-tenant assignment (a workspace/team from another organization, or a
membership scope outside the organization) is rejected here, not left to the persistence layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from northstar.kernel.errors import Diagnostic, KernelError

# Resource + capability vocabulary (stable contracts, docs/07 §6).
RES_ORGANIZATION = "organization.organization"
RES_WORKSPACE = "organization.workspace"
RES_TEAM = "organization.team"
RES_MEMBERSHIP = "organization.membership"


class OrgRole(StrEnum):
    """Organization RBAC roles (permission bundles, docs/07 §5)."""

    ORG_ADMIN = "org_admin"
    WORKSPACE_ADMIN = "workspace_admin"
    MEMBER = "member"
    BILLING_MANAGER = "billing_manager"
    AUDITOR = "auditor"


# Separation-of-duty: an auditor must not also administer or manage billing for the same org.
SEPARATION_OF_DUTY: tuple[frozenset[str], ...] = (
    frozenset({OrgRole.AUDITOR.value, OrgRole.ORG_ADMIN.value}),
    frozenset({OrgRole.AUDITOR.value, OrgRole.BILLING_MANAGER.value}),
)


class OrganizationError(KernelError):
    """Base class for organization domain errors."""


class OrganizationInvariantViolation(OrganizationError):  # noqa: N818 canonical error name
    """A tenancy or membership invariant was violated (e.g. cross-tenant scope)."""

    def __init__(self, message: str, code: str = "organization.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class UnknownRole(OrganizationError):  # noqa: N818 canonical error name
    """An assigned role is not a recognised organization role."""

    def __init__(self, role: str) -> None:
        message = "the assigned role is not recognised"
        super().__init__(message, (Diagnostic(code="organization.role.unknown", message=message),))
        self.role = role


class SeparationOfDutyViolation(OrganizationError):  # noqa: N818 canonical error name
    """An assigned role set violates a separation-of-duty rule (docs/07 §5)."""

    def __init__(self) -> None:
        message = "the requested role set violates a separation-of-duty rule"
        super().__init__(
            message, (Diagnostic(code="organization.role.sod-violation", message=message),)
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OrganizationInvariantViolation(message)


@dataclass(frozen=True, slots=True)
class Organization:
    """The tenant root. Its ``organization_id`` *is* the tenant scope for owned records."""

    organization_id: str
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.organization_id), "organization_id must be non-empty")
        _require(len(self.name.strip()) >= 2, "organization name must be at least 2 characters")

    @property
    def tenant_scope(self) -> str:
        return self.organization_id


@dataclass(frozen=True, slots=True)
class Workspace:
    """A workspace nested under exactly one organization (carries the tenant scope)."""

    workspace_id: str
    organization_id: str
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.workspace_id), "workspace_id must be non-empty")
        _require(bool(self.organization_id), "workspace.organization_id must be non-empty")
        _require(len(self.name.strip()) >= 2, "workspace name must be at least 2 characters")


@dataclass(frozen=True, slots=True)
class Team:
    """A team nested under a workspace within an organization (carries the tenant scope)."""

    team_id: str
    workspace_id: str
    organization_id: str
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.team_id), "team_id must be non-empty")
        _require(bool(self.workspace_id), "team.workspace_id must be non-empty")
        _require(bool(self.organization_id), "team.organization_id must be non-empty")
        _require(len(self.name.strip()) >= 2, "team name must be at least 2 characters")


@dataclass(frozen=True, slots=True)
class Membership:
    """Binds a subject to an organization (optionally workspace/team) with a role.

    The membership scope must lie *within* the organization: a workspace/team from another tenant
    is a cross-tenant assignment and is rejected (docs/07 §9). ``roles`` is validated against
    separation-of-duty rules (docs/07 §5).
    """

    membership_id: str
    subject_id: str
    organization_id: str
    roles: frozenset[str]
    created_at: datetime
    workspace_id: str | None = None
    team_id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.membership_id), "membership_id must be non-empty")
        _require(bool(self.subject_id), "membership.subject_id must be non-empty")
        _require(bool(self.organization_id), "membership.organization_id must be non-empty")
        _require(bool(self.roles), "membership must have at least one role")
        for role in self.roles:
            if role not in {r.value for r in OrgRole}:
                raise UnknownRole(role)
        for rule in SEPARATION_OF_DUTY:
            if len(rule & self.roles) >= 2:
                raise SeparationOfDutyViolation()

    def with_role(self, role: str) -> Membership:
        """Return a copy with ``role`` added, re-validating SoD (raises on conflict)."""
        from dataclasses import replace

        return replace(self, roles=frozenset({*self.roles, role}))


def validate_workspace_within(organization: Organization, workspace: Workspace) -> None:
    """Raise unless ``workspace`` belongs to ``organization`` (cross-tenant guard)."""
    _require(
        workspace.organization_id == organization.organization_id,
        "workspace does not belong to the organization",
    )


def validate_team_within(workspace: Workspace, team: Team) -> None:
    """Raise unless ``team`` belongs to ``workspace`` and its organization (cross-tenant guard)."""
    _require(team.workspace_id == workspace.workspace_id, "team does not belong to the workspace")
    _require(
        team.organization_id == workspace.organization_id,
        "team organization does not match the workspace",
    )
