"""SEO surface: an auto-generated ``sitemap.xml`` + ``robots.txt`` for the public site.

The sitemap is built from the CURRENT published knowledge documents (via the browse query on the
public tenant), so newly published tutorials appear automatically the next time a crawler fetches it
-- no manual step. URLs point at the web frontend's crawlable, path-based routes. The site base URL is
configured with ``NORTHSTAR_SITE_URL`` (default ``http://localhost:5173``).
"""

from __future__ import annotations

import uuid
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.messaging import Query, QueryBus
from northstar.modules.knowledge.application.capabilities import (
    CAP_BROWSE_DOCUMENTS,
    CAP_VERSION,
    BrowseDocumentsQuery,
)

_STATE_KEY = "northstar_seo_dependencies"
_PAGE = 1000


class SeoDependencies:
    def __init__(self, *, query_bus: QueryBus, public_tenant: str, site_url: str) -> None:
        self.query_bus = query_bus
        self.public_tenant = public_tenant
        self.site_url = site_url.rstrip("/")


def bind_seo_dependencies(app_state: object, deps: SeoDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> SeoDependencies:
    return getattr(request.app.state, _STATE_KEY)


def _context(tenant: str) -> RequestContext:
    return RequestContext(
        actor=Actor(type=ActorType.ANONYMOUS, id="crawler"),
        correlation_id=f"seo_{uuid.uuid4().hex}",
        tenant_scope=tenant,
    )


def create_seo_router() -> APIRouter:
    router = APIRouter(tags=["seo"])

    @router.get("/robots.txt")
    def robots(request: Request) -> PlainTextResponse:
        site = _deps(request).site_url
        body = f"User-agent: *\nAllow: /\nSitemap: {site}/sitemap.xml\n"
        return PlainTextResponse(body)

    @router.get("/sitemap.xml")
    def sitemap(request: Request) -> Response:
        deps = _deps(request)
        ctx = _context(deps.public_tenant)
        site = deps.site_url
        urls: list[str] = [f"{site}/"]
        categories: set[str] = set()
        offset = 0
        while True:
            result = deps.query_bus.dispatch(
                Query(
                    capability=CAP_BROWSE_DOCUMENTS,
                    version=CAP_VERSION,
                    parameters=BrowseDocumentsQuery(limit=_PAGE, offset=offset),
                ),
                ctx,
            ).value
            entries = result.entries
            if not entries:
                break
            for e in entries:
                cat = (e.terms.get("category") or ["_"])[0]
                categories.add(cat)
                if e.revision_id:
                    urls.append(f"{site}/l/{cat}/{e.revision_id}")
            if len(entries) < _PAGE:
                break
            offset += _PAGE
        urls.extend(f"{site}/c/{cat}" for cat in sorted(categories) if cat != "_")

        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for u in urls:
            parts.append(f"<url><loc>{escape(u)}</loc></url>")
        parts.append("</urlset>")
        return Response(content="\n".join(parts), media_type="application/xml")

    return router
