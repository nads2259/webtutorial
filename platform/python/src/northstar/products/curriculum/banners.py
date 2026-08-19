"""Prepend a topic banner image block to each curriculum lesson.

Per lesson the updater runs:
``knowledge.revision.get`` -> ``knowledge.draft.edit`` -> ``knowledge.document.submit`` ->
``knowledge.document.publish``. Banner URLs are deterministic from category + module taxonomy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus
from northstar.modules.knowledge.application import capabilities as knowledge

from .diagrams import _is_generated_or_generic

DEFAULT_TENANT = "org-bestinfopages"
ACTOR_ID = "curriculum-banner"
BANNER_ATTR = "role"
BANNER_ROLE = "banner"
BANNER_ID_PREFIX = "topic-banner-"
BANNER_COUNT = 36
_FLAGSHIP: dict[str, str] = {
    "PY-C00-M01-L01": "/img/banners/flagship-01.svg",
    "PY-C00-M02-L01": "/img/banners/flagship-02.svg",
    "PY-C01-M01-L01": "/img/banners/flagship-03.svg",
    "PY-C02-M01-L01": "/img/banners/flagship-04.svg",
    "PY-C03-M01-L01": "/img/banners/flagship-05.svg",
    "PY-C05-M01-L01": "/img/banners/flagship-06.svg",
    "PY-C10-M01-L01": "/img/banners/flagship-07.svg",
    "PY-C20-M01-L01": "/img/banners/flagship-08.svg",
}


def _hash_code(value: str) -> int:
    h = 2166136261
    for ch in value:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def banner_for_module(category: str, module: str) -> str:
    key = f"{category}:{module or '_root'}"
    idx = (_hash_code(key) % BANNER_COUNT) + 1
    return f"/img/banners/banner-{idx:02d}.svg"


def banner_for_lesson(lesson_id: str | None, category: str, module: str) -> str:
    if lesson_id and lesson_id.upper() in _FLAGSHIP:
        return _FLAGSHIP[lesson_id.upper()]
    return banner_for_module(category, module)


def _is_banner_block(block: dict[str, Any]) -> bool:
    if str(block.get("id", "")).startswith(BANNER_ID_PREFIX):
        return True
    if block.get("type") != "image":
        return False
    attrs = (block.get("data") or {}).get("attributes") or {}
    return attrs.get(BANNER_ATTR) == BANNER_ROLE


def _rebuild(blocks: list[dict[str, Any]], banner: dict[str, Any]) -> list[dict[str, Any]]:
    kept = [b for b in blocks if not _is_banner_block(b)]
    return [banner, *kept]


@dataclass(slots=True)
class BannerStats:
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class BannerUpdater:
    def __init__(self, *, command_bus: CommandBus, query_bus: QueryBus, tenant: str) -> None:
        self._cb = command_bus
        self._qb = query_bus
        self._tenant = tenant

    def _ctx(self) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=ACTOR_ID),
            correlation_id=f"banner-{uuid.uuid4().hex}",
            tenant_scope=self._tenant,
        )

    def update(
        self,
        *,
        object_id: str,
        revision_id: str,
        title: str,
        summary: str | None,
        visibility: str,
        category: str,
        module: str,
        lesson_id: str | None,
    ) -> str:
        ctx = self._ctx()
        rev = self._qb.dispatch(
            Query(
                capability=knowledge.CAP_GET_REVISION,
                version=knowledge.CAP_VERSION,
                parameters=knowledge.GetRevisionQuery(revision_id=revision_id),
            ),
            ctx,
        ).value
        blocks = [dict(b) for b in rev.blocks]
        # Drop stale generated diagrams while adding the banner.
        blocks = [b for b in blocks if not _is_generated_or_generic(b)]
        src = banner_for_lesson(lesson_id, category, module)
        slug = re.sub(r"[^a-z0-9]", "", object_id.lower())[:16]
        banner = {
            "id": f"{BANNER_ID_PREFIX}{slug}",
            "type": "image",
            "version": 1,
            "data": {
                "attributes": {"alt": title, BANNER_ATTR: BANNER_ROLE},
                "content": src,
            },
            "children": [],
        }
        rebuilt = _rebuild(blocks, banner)
        self._cb.dispatch(
            Command(
                capability=knowledge.CAP_EDIT_DRAFT,
                version=knowledge.CAP_VERSION,
                payload=knowledge.EditDraftCommand(object_id=object_id, blocks=tuple(rebuilt)),
            ),
            ctx,
        )
        self._cb.dispatch(
            Command(
                capability=knowledge.CAP_SUBMIT_FOR_REVIEW,
                version=knowledge.CAP_VERSION,
                payload=knowledge.SubmitForReviewCommand(object_id=object_id),
            ),
            ctx,
        )
        published = self._cb.dispatch(
            Command(
                capability=knowledge.CAP_PUBLISH_DOCUMENT,
                version=knowledge.CAP_VERSION,
                payload=knowledge.PublishDocumentCommand(
                    object_id=object_id,
                    title=title,
                    visibility=visibility,
                    summary=summary,
                ),
            ),
            ctx,
        ).value
        return published.revision_id


def _load_ledger(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


def _catalog(database_url: str | None) -> list[dict[str, Any]]:
    import os

    import sqlalchemy as sa

    url = database_url or os.environ["DATABASE_URL"]
    engine = sa.create_engine(url)
    rows: list[dict[str, Any]] = []
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT DISTINCT ON (p.object_id)
                    p.object_id, p.revision_id, p.visibility, r.title, r.summary
                FROM northstar_knowledge.publication p
                JOIN northstar_knowledge.revision r ON r.revision_id = p.revision_id
                ORDER BY p.object_id, p.published_at DESC
                """
            )
        )
        for object_id, revision_id, visibility, title, summary in result:
            terms: dict[str, str] = {}
            for scheme, term in conn.execute(
                sa.text(
                    """
                    SELECT scheme, term FROM northstar_knowledge.taxonomy_assignment
                    WHERE object_id = :oid
                    """
                ),
                {"oid": object_id},
            ):
                terms[str(scheme)] = str(term)
            rows.append(
                {
                    "object_id": str(object_id),
                    "revision_id": str(revision_id),
                    "visibility": visibility or "organization",
                    "title": title,
                    "summary": summary,
                    "category": terms.get("category", ""),
                    "module": terms.get("module", ""),
                    "lesson_id": terms.get("lesson"),
                }
            )
    return rows


def run(
    *,
    database_url: str | None,
    limit: int | None,
    ledger_path: Path,
    tenant: str,
) -> BannerStats:
    from northstar.products.reference.assembly import assemble_reference_product

    product = assemble_reference_product(database_url=database_url)
    updater = BannerUpdater(
        command_bus=product.command_bus,
        query_bus=product.query_bus,
        tenant=tenant,
    )
    catalog = _catalog(database_url)
    if limit is not None:
        catalog = catalog[:limit]

    ledger = _load_ledger(ledger_path)
    stats = BannerStats()
    total = len(catalog)
    started = time.monotonic()
    for i, row in enumerate(catalog, start=1):
        oid = row["object_id"]
        prior = ledger.get(oid)
        if prior and prior.get("done"):
            stats.skipped += 1
            continue
        try:
            new_rev = updater.update(
                object_id=oid,
                revision_id=row["revision_id"],
                title=row["title"],
                summary=row["summary"],
                visibility=row["visibility"],
                category=row["category"],
                module=row["module"],
                lesson_id=row.get("lesson_id"),
            )
            ledger[oid] = {"revision_id": new_rev, "done": True}
            stats.updated += 1
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            stats.errors.append(f"{oid}: {exc}")
            print(f"  [fail] {oid}: {exc}", file=sys.stderr, flush=True)
        if i % 25 == 0 or i == total:
            _save_ledger(ledger_path, ledger)
            rate = i / max(time.monotonic() - started, 1e-6)
            eta = (total - i) / max(rate, 1e-6)
            print(
                f"  {i}/{total} (updated={stats.updated} skipped={stats.skipped} "
                f"failed={stats.failed}) {rate:.1f}/s eta={eta / 60:.1f}m",
                flush=True,
            )
    _save_ledger(ledger_path, ledger)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m northstar.products.curriculum.banners",
        description="Prepend topic banner image blocks to curriculum lessons.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ledger", default="curriculum-banner-ledger.json")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    args = parser.parse_args(argv)

    stats = run(
        database_url=args.database_url,
        limit=args.limit,
        ledger_path=Path(args.ledger),
        tenant=args.tenant,
    )
    print(
        f"done: updated={stats.updated} skipped={stats.skipped} failed={stats.failed}",
        flush=True,
    )
    return 1 if stats.failed and not stats.updated else 0


if __name__ == "__main__":
    raise SystemExit(main())
