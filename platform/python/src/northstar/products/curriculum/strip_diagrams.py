"""Remove generated/generic Mermaid diagram blocks from curriculum lessons.

Runs the authoritative knowledge pipeline per document:
``knowledge.revision.get`` -> ``knowledge.draft.edit`` -> ``knowledge.document.submit`` ->
``knowledge.document.publish``. Resumable via a JSON ledger.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus
from northstar.modules.knowledge.application import capabilities as knowledge

from .diagrams import GEN_ATTR, GEN_ID_PREFIX, _is_generated_or_generic

DEFAULT_TENANT = "org-bestinfopages"
ACTOR_ID = "curriculum-strip-diagrams"


@dataclass(slots=True)
class StripStats:
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class DiagramStripper:
    def __init__(self, *, command_bus: CommandBus, query_bus: QueryBus, tenant: str) -> None:
        self._cb = command_bus
        self._qb = query_bus
        self._tenant = tenant

    def _ctx(self) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=ACTOR_ID),
            correlation_id=f"strip-{uuid.uuid4().hex}",
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
    ) -> str | None:
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
        if not any(_is_generated_or_generic(b) for b in blocks):
            return None
        kept = [b for b in blocks if not _is_generated_or_generic(b)]
        self._cb.dispatch(
            Command(
                capability=knowledge.CAP_EDIT_DRAFT,
                version=knowledge.CAP_VERSION,
                payload=knowledge.EditDraftCommand(object_id=object_id, blocks=tuple(kept)),
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
            rows.append(
                {
                    "object_id": str(object_id),
                    "revision_id": str(revision_id),
                    "visibility": visibility or "organization",
                    "title": title,
                    "summary": summary,
                }
            )
    return rows


def run(
    *,
    database_url: str | None,
    limit: int | None,
    ledger_path: Path,
    tenant: str,
) -> StripStats:
    from northstar.products.reference.assembly import assemble_reference_product

    product = assemble_reference_product(database_url=database_url)
    stripper = DiagramStripper(
        command_bus=product.command_bus,
        query_bus=product.query_bus,
        tenant=tenant,
    )
    catalog = _catalog(database_url)
    if limit is not None:
        catalog = catalog[:limit]

    ledger = _load_ledger(ledger_path)
    stats = StripStats()
    total = len(catalog)
    started = time.monotonic()
    for i, row in enumerate(catalog, start=1):
        oid = row["object_id"]
        prior = ledger.get(oid)
        if prior and prior.get("done"):
            stats.skipped += 1
            continue
        try:
            new_rev = stripper.update(
                object_id=oid,
                revision_id=row["revision_id"],
                title=row["title"],
                summary=row["summary"],
                visibility=row["visibility"],
            )
            if new_rev is None:
                stats.skipped += 1
                ledger[oid] = {"revision_id": row["revision_id"], "done": True, "unchanged": True}
            else:
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
        prog="python -m northstar.products.curriculum.strip_diagrams",
        description="Strip generated/generic diagram blocks from curriculum lessons.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ledger", default="curriculum-strip-ledger.json")
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
