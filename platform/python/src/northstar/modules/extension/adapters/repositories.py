"""Extension registries (in-memory + SQLAlchemy) for installations, catalog listings and themes.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values. The registry is the source of truth for the
enabled/disabled lifecycle state the dispatch guard consults (FR-EXT-005).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import (
    CatalogListing,
    ExtensionInstallation,
    ExtensionType,
    LifecycleState,
    Permission,
    Presentation,
    ThemeApplication,
    TrustTier,
    UninstallDataPolicy,
)
from .tables import ExtensionTables


def _now() -> datetime:
    return datetime.now(UTC)


def _permissions_to_json(permissions: tuple[Permission, ...]) -> list[dict[str, object]]:
    return [permission.to_dict() for permission in permissions]


def _permissions_from_json(raw: object) -> tuple[Permission, ...]:
    items: list[Permission] = []
    for entry in raw or ():  # type: ignore[union-attr]
        items.append(
            Permission(
                action=str(entry["action"]),
                resource_scope=(
                    str(entry["resource_scope"]) if entry.get("resource_scope") else None
                ),
                data_classifications=tuple(str(c) for c in entry.get("data_classifications", ())),
            )
        )
    return tuple(items)


def _presentation_to_json(presentation: Presentation) -> dict[str, object]:
    return {
        "theme_id": presentation.theme_id,
        "version": presentation.version,
        "tokens": {k: dict(v) for k, v in presentation.tokens.items()},
        "slots": list(presentation.slots),
        "modes": list(presentation.modes),
    }


def _presentation_from_json(raw: dict[str, object]) -> Presentation:
    return Presentation(
        theme_id=str(raw["theme_id"]),
        version=str(raw["version"]),
        tokens={str(k): dict(v) for k, v in dict(raw["tokens"]).items()},  # type: ignore[arg-type]
        slots=tuple(str(s) for s in raw.get("slots", ())),  # type: ignore[arg-type]
        modes=tuple(str(m) for m in raw.get("modes", ())),  # type: ignore[arg-type]
    )


class InMemoryExtensionRegistry:
    """In-memory registry for fast, deterministic unit/security tests (tenant-scoped)."""

    def __init__(self) -> None:
        self._installs: dict[tuple[str, str], ExtensionInstallation] = {}
        self._listings: dict[tuple[str, str, str], CatalogListing] = {}
        self._themes: dict[tuple[str, str], ThemeApplication] = {}

    def add(self, installation: ExtensionInstallation) -> None:
        self._installs[(installation.organization_id, installation.extension_id)] = installation

    def get(self, *, organization_id: str, extension_id: str) -> ExtensionInstallation | None:
        return self._installs.get((organization_id, extension_id))

    def replace(self, installation: ExtensionInstallation) -> None:
        self._installs[(installation.organization_id, installation.extension_id)] = installation

    def set_state(self, *, organization_id: str, extension_id: str, state: LifecycleState) -> None:
        from dataclasses import replace

        existing = self._installs[(organization_id, extension_id)]
        self._installs[(organization_id, extension_id)] = replace(existing, state=state)

    def remove(self, *, organization_id: str, extension_id: str) -> None:
        self._installs.pop((organization_id, extension_id), None)

    def publish_listing(self, listing: CatalogListing) -> None:
        key = (listing.organization_id, listing.extension_id, listing.version)
        self._listings[key] = listing

    def get_listing(self, *, organization_id: str, extension_id: str) -> CatalogListing | None:
        for (org, ext, _version), listing in self._listings.items():
            if org == organization_id and ext == extension_id:
                return listing
        return None

    def set_theme(self, application: ThemeApplication) -> None:
        self._themes[(application.organization_id, application.theme_id)] = application

    def get_theme(self, *, organization_id: str, theme_id: str) -> ThemeApplication | None:
        return self._themes.get((organization_id, theme_id))


class SqlAlchemyExtensionRegistry:
    """PostgreSQL registry; every query filters by ``organization_id`` and sets the tenant GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: ExtensionTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add(self, installation: ExtensionInstallation) -> None:
        table = self._tables.extension_installation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, installation.organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=installation.organization_id,
                    extension_id=installation.extension_id,
                    version=installation.version,
                    publisher_id=installation.publisher_id,
                    extension_type=installation.extension_type.value,
                    required_trust_tier=installation.required_trust_tier.value,
                    granted_trust_tier=installation.granted_trust_tier.value,
                    permissions=_permissions_to_json(installation.permissions),
                    package_digest=installation.package_digest,
                    uninstall_policy=installation.uninstall_policy.value,
                    state=installation.state.value,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            uow.commit()

    def get(self, *, organization_id: str, extension_id: str) -> ExtensionInstallation | None:
        table = self._tables.extension_installation
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.extension_id == extension_id,
                )
            ).first()
        if row is None:
            return None
        return ExtensionInstallation(
            organization_id=row.organization_id,
            extension_id=row.extension_id,
            version=row.version,
            publisher_id=row.publisher_id,
            extension_type=ExtensionType(row.extension_type),
            required_trust_tier=TrustTier(row.required_trust_tier),
            granted_trust_tier=TrustTier(row.granted_trust_tier),
            permissions=_permissions_from_json(row.permissions),
            package_digest=row.package_digest,
            uninstall_policy=UninstallDataPolicy(row.uninstall_policy),
            state=LifecycleState(row.state),
        )

    def replace(self, installation: ExtensionInstallation) -> None:
        table = self._tables.extension_installation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, installation.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.organization_id == installation.organization_id,
                    table.c.extension_id == installation.extension_id,
                )
                .values(
                    version=installation.version,
                    publisher_id=installation.publisher_id,
                    extension_type=installation.extension_type.value,
                    required_trust_tier=installation.required_trust_tier.value,
                    granted_trust_tier=installation.granted_trust_tier.value,
                    permissions=_permissions_to_json(installation.permissions),
                    package_digest=installation.package_digest,
                    uninstall_policy=installation.uninstall_policy.value,
                    state=installation.state.value,
                    updated_at=_now(),
                )
            )
            uow.commit()

    def set_state(self, *, organization_id: str, extension_id: str, state: LifecycleState) -> None:
        table = self._tables.extension_installation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.extension_id == extension_id,
                )
                .values(state=state.value, updated_at=_now())
            )
            uow.commit()

    def remove(self, *, organization_id: str, extension_id: str) -> None:
        table = self._tables.extension_installation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                delete(table).where(
                    table.c.organization_id == organization_id,
                    table.c.extension_id == extension_id,
                )
            )
            uow.commit()

    def publish_listing(self, listing: CatalogListing) -> None:
        table = self._tables.catalog_listing
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, listing.organization_id)
            existing = session.execute(
                select(table.c.version).where(
                    table.c.organization_id == listing.organization_id,
                    table.c.extension_id == listing.extension_id,
                    table.c.version == listing.version,
                )
            ).first()
            values = {
                "publisher_id": listing.publisher_id,
                "verified": listing.verified,
                "permissions": _permissions_to_json(listing.permissions),
            }
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=listing.organization_id,
                        extension_id=listing.extension_id,
                        version=listing.version,
                        created_at=_now(),
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == listing.organization_id,
                        table.c.extension_id == listing.extension_id,
                        table.c.version == listing.version,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_listing(self, *, organization_id: str, extension_id: str) -> CatalogListing | None:
        table = self._tables.catalog_listing
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.extension_id == extension_id,
                )
                .order_by(table.c.version.desc())
            ).first()
        if row is None:
            return None
        return CatalogListing(
            organization_id=row.organization_id,
            extension_id=row.extension_id,
            version=row.version,
            publisher_id=row.publisher_id,
            verified=bool(row.verified),
            permissions=_permissions_from_json(row.permissions),
        )

    def set_theme(self, application: ThemeApplication) -> None:
        table = self._tables.theme_application
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, application.organization_id)
            existing = session.execute(
                select(table.c.theme_id).where(
                    table.c.organization_id == application.organization_id,
                    table.c.theme_id == application.theme_id,
                )
            ).first()
            values = {
                "version": application.version,
                "presentation": _presentation_to_json(application.presentation),
                "updated_at": _now(),
            }
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=application.organization_id,
                        theme_id=application.theme_id,
                        created_at=_now(),
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == application.organization_id,
                        table.c.theme_id == application.theme_id,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_theme(self, *, organization_id: str, theme_id: str) -> ThemeApplication | None:
        table = self._tables.theme_application
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.theme_id == theme_id,
                )
            ).first()
        if row is None:
            return None
        return ThemeApplication(
            organization_id=row.organization_id,
            theme_id=row.theme_id,
            version=row.version,
            presentation=_presentation_from_json(dict(row.presentation)),
        )
