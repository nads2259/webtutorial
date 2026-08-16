"""Ports (abstractions) for the organization application layer (rule 10/20, DIP).

The repository port is role-specific and tenant-aware: every read/list is scoped by
``organization_id`` so a caller can never widen its query beyond its own tenant (rule 50, tenant
isolation). Adapters in :mod:`..adapters` implement it over PostgreSQL (with RLS defense-in-depth)
or in memory for unit tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.model import Membership, Organization, Team, Workspace


@runtime_checkable
class OrganizationRepositoryPort(Protocol):
    """Persists and reads the organization tenancy tree, always tenant-scoped."""

    def add_organization(self, organization: Organization) -> None: ...

    def get_organization(self, organization_id: str) -> Organization | None: ...

    def add_workspace(self, workspace: Workspace) -> None: ...

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None:
        """Return the workspace only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def add_team(self, team: Team) -> None: ...

    def add_membership(self, membership: Membership) -> None: ...

    def get_membership(self, *, organization_id: str, membership_id: str) -> Membership | None:
        """Return the membership only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def list_memberships(self, *, organization_id: str) -> Sequence[Membership]:
        """List memberships for exactly one organization (never cross-tenant)."""
        ...

    def list_role_bindings(self, *, subject_id: str) -> Sequence[Membership]:
        """All memberships held by a subject (used to derive RBAC bindings)."""
        ...
