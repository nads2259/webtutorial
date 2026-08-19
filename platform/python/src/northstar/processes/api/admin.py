"""Admin surface: a management-only ``GET /admin/stats`` for the dashboard.

Read-only aggregate counts across the owned schemas, gated to backend/management accounts via the
same ``admin_lookup`` used elsewhere. Every RLS-forced table is queried with the tenant GUC set so the
counts respect tenant isolation (rule 50). Missing tables (partial migrations) fail soft to 0.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.kernel.context import RequestContext

_STATE_KEY = "northstar_admin_api_dependencies"


@dataclass(frozen=True, slots=True)
class AdminApiDependencies:
    authenticate: Callable[[Request], "RequestContext | None"]
    admin_lookup: Callable[[str], bool]
    session_factory: sessionmaker[SaSession]
    tenant: str


def bind_admin_dependencies(app_state: object, deps: AdminApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> AdminApiDependencies:
    return getattr(request.app.state, _STATE_KEY)


def _scalar(session: SaSession, sql: str) -> int:
    try:
        return int(session.execute(sa.text(sql)).scalar() or 0)
    except Exception:  # noqa: BLE001 - a missing table/permission counts as 0
        session.rollback()
        return 0


def create_admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.get("/stats")
    def stats(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = deps.authenticate(request)
        if context is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication is required"})
        if not deps.admin_lookup(context.actor.id):
            return JSONResponse(status_code=403, content={"detail": "Admin access required"})

        with deps.session_factory() as session:
            set_tenant_guc(session, deps.tenant)
            documents = _scalar(
                session, "SELECT count(DISTINCT object_id) FROM northstar_knowledge.publication"
            )
            topics = _scalar(
                session,
                "SELECT count(DISTINCT term) FROM northstar_knowledge.taxonomy_assignment "
                "WHERE scheme = 'category'",
            )
            courses = _scalar(
                session,
                "SELECT count(DISTINCT term) FROM northstar_knowledge.taxonomy_assignment "
                "WHERE scheme = 'phase'",
            )
            emails = _scalar(session, "SELECT count(*) FROM northstar_messaging.email_message")
            code_runs = _scalar(session, "SELECT count(*) FROM northstar_codelab.code_run")
            users = _scalar(session, "SELECT count(*) FROM northstar_identity.password_credential")
            confirmed = _scalar(
                session,
                "SELECT count(*) FROM northstar_identity.password_credential "
                "WHERE email_verified = true",
            )
            recent_rows: list[dict[str, str]] = []
            try:
                for row in session.execute(
                    sa.text(
                        "SELECT event_type, detail, created_at FROM northstar_identity.account_event "
                        "ORDER BY created_at DESC LIMIT 8"
                    )
                ):
                    recent_rows.append(
                        {
                            "event_type": row[0],
                            "detail": row[1],
                            "created_at": row[2].isoformat() if row[2] else "",
                        }
                    )
            except Exception:  # noqa: BLE001
                session.rollback()

        return JSONResponse(
            status_code=200,
            content={
                "documents": documents,
                "topics": topics,
                "courses": courses,
                "emails": emails,
                "code_runs": code_runs,
                "users": users,
                "confirmed_users": confirmed,
                "recent": recent_rows,
            },
        )

    return router
