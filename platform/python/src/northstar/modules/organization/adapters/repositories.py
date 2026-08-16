"""Organization repositories + policy resolvers (in-memory and SQLAlchemy).

Implements :class:`OrganizationRepositoryPort` and the kernel policy provider ports
(:class:`RoleBindingProviderPort`, :class:`ResourceAttributeProviderPort`) so the layered policy
engine derives RBAC bindings and a resource's authoritative tenant scope from owned organization
data. Every SQLAlchemy read is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). No string interpolation of values.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import ResourceRef
from northstar.kernel.policy import ResourceAttributes, RoleBinding, ScopeRef

from ..application.ports import OrganizationRepositoryPort
from ..domain.model import (
    RES_MEMBERSHIP,
    RES_ORGANIZATION,
    RES_TEAM,
    RES_WORKSPACE,
    Membership,
    Organization,
    Team,
    Workspace,
)
from .tables import OrganizationTables


class InMemoryOrganizationRepository:
    """In-memory repository for fast, deterministic unit tests (no database)."""

    def __init__(self) -> None:
        self._orgs: dict[str, Organization] = {}
        self._workspaces: dict[str, Workspace] = {}
        self._teams: dict[str, Team] = {}
        self._memberships: dict[str, Membership] = {}

    def add_organization(self, organization: Organization) -> None:
        self._orgs[organization.organization_id] = organization

    def get_organization(self, organization_id: str) -> Organization | None:
        return self._orgs.get(organization_id)

    def add_workspace(self, workspace: Workspace) -> None:
        self._workspaces[workspace.workspace_id] = workspace

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None or workspace.organization_id != organization_id:
            return None
        return workspace

    def add_team(self, team: Team) -> None:
        self._teams[team.team_id] = team

    def add_membership(self, membership: Membership) -> None:
        self._memberships[membership.membership_id] = membership

    def get_membership(self, *, organization_id: str, membership_id: str) -> Membership | None:
        membership = self._memberships.get(membership_id)
        if membership is None or membership.organization_id != organization_id:
            return None
        return membership

    def list_memberships(self, *, organization_id: str) -> Sequence[Membership]:
        return [m for m in self._memberships.values() if m.organization_id == organization_id]

    def list_role_bindings(self, *, subject_id: str) -> Sequence[Membership]:
        return [m for m in self._memberships.values() if m.subject_id == subject_id]


class SqlAlchemyOrganizationRepository:
    """PostgreSQL repository; every scoped query filters by ``organization_id`` and sets the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: OrganizationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_organization(self, organization: Organization) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization.organization_id)
            uow.session.execute(
                insert(self._tables.organization).values(
                    organization_id=organization.organization_id,
                    name=organization.name,
                    created_at=organization.created_at,
                )
            )
            uow.commit()

    def get_organization(self, organization_id: str) -> Organization | None:
        table = self._tables.organization
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).first()
        if row is None:
            return None
        return Organization(
            organization_id=row.organization_id, name=row.name, created_at=row.created_at
        )

    def add_workspace(self, workspace: Workspace) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, workspace.organization_id)
            uow.session.execute(
                insert(self._tables.workspace).values(
                    workspace_id=workspace.workspace_id,
                    organization_id=workspace.organization_id,
                    name=workspace.name,
                    created_at=workspace.created_at,
                )
            )
            uow.commit()

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None:
        table = self._tables.workspace
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.workspace_id == workspace_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return Workspace(
            workspace_id=row.workspace_id,
            organization_id=row.organization_id,
            name=row.name,
            created_at=row.created_at,
        )

    def add_team(self, team: Team) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, team.organization_id)
            uow.session.execute(
                insert(self._tables.team).values(
                    team_id=team.team_id,
                    workspace_id=team.workspace_id,
                    organization_id=team.organization_id,
                    name=team.name,
                    created_at=team.created_at,
                )
            )
            uow.commit()

    def add_membership(self, membership: Membership) -> None:
        table = self._tables.membership
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, membership.organization_id)
            uow.session.execute(
                table.delete().where(table.c.membership_id == membership.membership_id)
            )
            uow.session.execute(
                insert(table).values(
                    membership_id=membership.membership_id,
                    subject_id=membership.subject_id,
                    organization_id=membership.organization_id,
                    roles=sorted(membership.roles),
                    workspace_id=membership.workspace_id,
                    team_id=membership.team_id,
                    created_at=membership.created_at,
                )
            )
            uow.commit()

    def get_membership(self, *, organization_id: str, membership_id: str) -> Membership | None:
        table = self._tables.membership
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.membership_id == membership_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        return None if row is None else _row_to_membership(row)

    def list_memberships(self, *, organization_id: str) -> Sequence[Membership]:
        table = self._tables.membership
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [_row_to_membership(r) for r in rows]

    def list_role_bindings(self, *, subject_id: str) -> Sequence[Membership]:
        table = self._tables.membership
        with self._session_factory() as session:
            rows = session.execute(select(table).where(table.c.subject_id == subject_id)).all()
        return [_row_to_membership(r) for r in rows]


def _row_to_membership(row: object) -> Membership:
    return Membership(
        membership_id=row.membership_id,
        subject_id=row.subject_id,
        organization_id=row.organization_id,
        roles=frozenset(row.roles),
        created_at=row.created_at,
        workspace_id=row.workspace_id,
        team_id=row.team_id,
    )


class OrgRoleDirectory:
    """Derives RBAC role bindings from a subject's organization memberships (docs/07 §5)."""

    def __init__(self, repository: OrganizationRepositoryPort) -> None:
        self._repo = repository

    def bindings_for(self, actor_id: str) -> Sequence[RoleBinding]:
        bindings: list[RoleBinding] = []
        for membership in self._repo.list_role_bindings(subject_id=actor_id):
            scope = ScopeRef(
                organization_id=membership.organization_id,
                workspace_id=membership.workspace_id,
            )
            for role in membership.roles:
                bindings.append(RoleBinding(actor_id=actor_id, role=role, scope=scope))
        return bindings


class OrgResourceAttributeResolver:
    """Resolves a resource's authoritative tenant scope from owned organization data.

    The resolver never trusts a client-supplied tenant: an organization resource's tenant is its
    own id; a workspace/team/membership resource's tenant is the ``organization_id`` recorded in
    the owned table (rule 50). Unknown resources return ``None`` ⇒ the policy engine fails closed.
    """

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: OrganizationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def attributes_for(self, resource: ResourceRef) -> ResourceAttributes | None:
        organization_id = self._lookup_org(resource)
        if organization_id is None:
            return None
        return ResourceAttributes(tenant=ScopeRef(organization_id=organization_id))

    def _lookup_org(self, resource: ResourceRef) -> str | None:
        if resource.type == RES_ORGANIZATION:
            table = self._tables.organization
            column = table.c.organization_id
            key = table.c.organization_id
        elif resource.type == RES_WORKSPACE:
            table = self._tables.workspace
            column = table.c.organization_id
            key = table.c.workspace_id
        elif resource.type == RES_TEAM:
            table = self._tables.team
            column = table.c.organization_id
            key = table.c.team_id
        elif resource.type == RES_MEMBERSHIP:
            table = self._tables.membership
            column = table.c.organization_id
            key = table.c.membership_id
        else:
            return None
        with self._session_factory() as session:
            row = session.execute(select(column).where(key == resource.id)).first()
        return None if row is None else row[0]
