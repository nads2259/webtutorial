"""Organization capabilities: one authoritative implementation per action (LAW-04, docs/07 §5,§9).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the request payload (rule 50, tenant
isolation): a caller cannot create a workspace, add a membership or list members for an
organization other than its own. Handlers depend only on :mod:`.ports` and the pure
:mod:`..domain`.

This module also exposes the module's authorization vocabulary — action definitions, role bundles
and relationship grants — for the composition root to configure the layered policy engine.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from northstar.kernel.policy import (
    ActionDefinition,
    RelationGrant,
    ResourceScope,
    RoleDefinition,
)

from ..domain.model import (
    RES_MEMBERSHIP,
    RES_ORGANIZATION,
    RES_TEAM,
    RES_WORKSPACE,
    Membership,
    Organization,
    OrganizationInvariantViolation,
    OrgRole,
    Team,
    Workspace,
    validate_team_within,
    validate_workspace_within,
)
from .ports import OrganizationRepositoryPort

CAP_VERSION = "1.0.0"

CAP_CREATE_ORGANIZATION = "organization.organization.create"
CAP_CREATE_WORKSPACE = "organization.workspace.create"
CAP_CREATE_TEAM = "organization.team.create"
CAP_ADD_MEMBERSHIP = "organization.membership.add"
CAP_ASSIGN_ROLE = "organization.role.assign"
CAP_LIST_MEMBERSHIPS = "organization.membership.list"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class TenantScopeMissing(OrganizationInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="organization.tenant.missing",
        )


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateOrganizationCommand:
    name: str


@dataclass(frozen=True, slots=True)
class CreateOrganizationResult:
    organization_id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateWorkspaceCommand:
    name: str


@dataclass(frozen=True, slots=True)
class CreateWorkspaceResult:
    workspace_id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class CreateTeamCommand:
    workspace_id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateTeamResult:
    team_id: str
    workspace_id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class AddMembershipCommand:
    subject_id: str
    roles: frozenset[str]
    workspace_id: str | None = None
    team_id: str | None = None


@dataclass(frozen=True, slots=True)
class AddMembershipResult:
    membership_id: str
    organization_id: str
    subject_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssignRoleCommand:
    membership_id: str
    role: str


@dataclass(frozen=True, slots=True)
class AssignRoleResult:
    membership_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ListMembershipsQuery:
    pass


@dataclass(frozen=True, slots=True)
class MembershipView:
    membership_id: str
    subject_id: str
    organization_id: str
    roles: tuple[str, ...]
    workspace_id: str | None
    team_id: str | None


@dataclass(frozen=True, slots=True)
class ListMembershipsResult:
    organization_id: str
    memberships: tuple[MembershipView, ...]


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    """Return the authenticated tenant scope, or fail closed (never trust the payload, rule 50)."""
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class CreateOrganization:
    """``organization.organization.create`` — create a new tenant root (platform action)."""

    def __init__(
        self, *, repository: OrganizationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateOrganizationResult:
        command = _typed(request, CreateOrganizationCommand)
        organization = Organization(
            organization_id=self._id_factory(), name=command.name, created_at=self._clock()
        )
        self._repo.add_organization(organization)
        return CreateOrganizationResult(
            organization_id=organization.organization_id, name=organization.name
        )


class CreateWorkspace:
    """``organization.workspace.create`` — create a workspace within the caller's tenant."""

    def __init__(
        self, *, repository: OrganizationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateWorkspaceResult:
        command = _typed(request, CreateWorkspaceCommand)
        organization_id = _tenant(request)
        workspace = Workspace(
            workspace_id=self._id_factory(),
            organization_id=organization_id,
            name=command.name,
            created_at=self._clock(),
        )
        self._repo.add_workspace(workspace)
        return CreateWorkspaceResult(
            workspace_id=workspace.workspace_id, organization_id=organization_id
        )


class CreateTeam:
    """``organization.team.create`` — create a team under a workspace in the caller's tenant."""

    def __init__(
        self, *, repository: OrganizationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateTeamResult:
        command = _typed(request, CreateTeamCommand)
        organization_id = _tenant(request)
        workspace = self._repo.get_workspace(
            organization_id=organization_id, workspace_id=command.workspace_id
        )
        if workspace is None:
            # The workspace is absent or belongs to another tenant: fail closed, do not disclose.
            raise OrganizationInvariantViolation(
                "workspace is not available in this scope",
                code="organization.workspace.not_found",
            )
        team = Team(
            team_id=self._id_factory(),
            workspace_id=workspace.workspace_id,
            organization_id=organization_id,
            name=command.name,
            created_at=self._clock(),
        )
        organization = self._repo.get_organization(organization_id)
        if organization is not None:
            validate_workspace_within(organization, workspace)
        validate_team_within(workspace, team)
        self._repo.add_team(team)
        return CreateTeamResult(
            team_id=team.team_id,
            workspace_id=team.workspace_id,
            organization_id=organization_id,
        )


class AddMembership:
    """``organization.membership.add`` — add a member to the caller's tenant (roles validated)."""

    def __init__(
        self, *, repository: OrganizationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AddMembershipResult:
        command = _typed(request, AddMembershipCommand)
        organization_id = _tenant(request)
        if command.workspace_id is not None:
            workspace = self._repo.get_workspace(
                organization_id=organization_id, workspace_id=command.workspace_id
            )
            if workspace is None:
                raise OrganizationInvariantViolation(
                    "workspace is not available in this scope",
                    code="organization.workspace.not_found",
                )
        membership = Membership(
            membership_id=self._id_factory(),
            subject_id=command.subject_id,
            organization_id=organization_id,
            roles=frozenset(command.roles),
            created_at=self._clock(),
            workspace_id=command.workspace_id,
            team_id=command.team_id,
        )
        self._repo.add_membership(membership)
        return AddMembershipResult(
            membership_id=membership.membership_id,
            organization_id=organization_id,
            subject_id=membership.subject_id,
            roles=tuple(sorted(membership.roles)),
        )


class AssignRole:
    """``organization.role.assign`` — add a role to a membership in the caller's tenant."""

    def __init__(self, *, repository: OrganizationRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> AssignRoleResult:
        command = _typed(request, AssignRoleCommand)
        organization_id = _tenant(request)
        membership = self._repo.get_membership(
            organization_id=organization_id, membership_id=command.membership_id
        )
        if membership is None:
            raise OrganizationInvariantViolation(
                "membership is not available in this scope",
                code="organization.membership.not_found",
            )
        updated = membership.with_role(command.role)  # re-validates SoD in the domain
        self._repo.add_membership(updated)
        return AssignRoleResult(
            membership_id=updated.membership_id, roles=tuple(sorted(updated.roles))
        )


class ListMemberships:
    """``organization.membership.list`` (query) — list the caller's tenant memberships only."""

    def __init__(self, *, repository: OrganizationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ListMembershipsResult:
        _typed(request, ListMembershipsQuery)
        organization_id = _tenant(request)
        rows = self._repo.list_memberships(organization_id=organization_id)
        views = tuple(
            MembershipView(
                membership_id=m.membership_id,
                subject_id=m.subject_id,
                organization_id=m.organization_id,
                roles=tuple(sorted(m.roles)),
                workspace_id=m.workspace_id,
                team_id=m.team_id,
            )
            for m in rows
        )
        return ListMembershipsResult(organization_id=organization_id, memberships=views)


# ---------------------------------------------------------------------------
# Authorization vocabulary for the layered policy engine (docs/07 §5-6)
# ---------------------------------------------------------------------------

# Resource-read actions used for tenant-isolation enforcement at the policy layer.
CAP_READ_ORGANIZATION = "organization.organization.read"
CAP_READ_WORKSPACE = "organization.workspace.read"
CAP_READ_MEMBERSHIP = "organization.membership.read"


def organization_action_definitions() -> tuple[ActionDefinition, ...]:
    """Governed actions and their required resource scope (tenant-bound ⇒ fail closed)."""
    return (
        # Creating an organization mints a new tenant, so it is a global/platform action.
        ActionDefinition(CAP_CREATE_ORGANIZATION, ResourceScope.GLOBAL),
        ActionDefinition(CAP_CREATE_WORKSPACE, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_CREATE_TEAM, ResourceScope.WORKSPACE),
        ActionDefinition(CAP_ADD_MEMBERSHIP, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_ASSIGN_ROLE, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_LIST_MEMBERSHIPS, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_READ_ORGANIZATION, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_READ_WORKSPACE, ResourceScope.ORGANIZATION),
        ActionDefinition(CAP_READ_MEMBERSHIP, ResourceScope.ORGANIZATION),
    )


def organization_role_definitions() -> tuple[RoleDefinition, ...]:
    """Role bundles (docs/07 §5): permissions attached to roles, not hardcoded conditionals."""
    admin_actions = frozenset(
        {
            CAP_CREATE_WORKSPACE,
            CAP_CREATE_TEAM,
            CAP_ADD_MEMBERSHIP,
            CAP_ASSIGN_ROLE,
            CAP_LIST_MEMBERSHIPS,
            CAP_READ_ORGANIZATION,
            CAP_READ_WORKSPACE,
            CAP_READ_MEMBERSHIP,
        }
    )
    read_actions = frozenset(
        {
            CAP_LIST_MEMBERSHIPS,
            CAP_READ_ORGANIZATION,
            CAP_READ_WORKSPACE,
            CAP_READ_MEMBERSHIP,
        }
    )
    return (
        RoleDefinition(OrgRole.ORG_ADMIN.value, admin_actions),
        RoleDefinition(
            OrgRole.WORKSPACE_ADMIN.value,
            frozenset({CAP_CREATE_TEAM, CAP_READ_WORKSPACE, CAP_READ_MEMBERSHIP}),
        ),
        RoleDefinition(OrgRole.MEMBER.value, read_actions),
        RoleDefinition(OrgRole.AUDITOR.value, read_actions),
        RoleDefinition(OrgRole.BILLING_MANAGER.value, frozenset({CAP_READ_ORGANIZATION})),
    )


def organization_relation_grants() -> tuple[RelationGrant, ...]:
    """Relationship-based grants (docs/07 §5): the resource owner may read/administer it."""
    return (
        RelationGrant(
            "owner",
            frozenset({CAP_READ_ORGANIZATION, CAP_READ_WORKSPACE, CAP_READ_MEMBERSHIP}),
        ),
    )


ORG_RESOURCE_TYPES: tuple[str, ...] = (RES_ORGANIZATION, RES_WORKSPACE, RES_TEAM, RES_MEMBERSHIP)
