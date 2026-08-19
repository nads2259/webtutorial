"""Remove stray/duplicate curriculum documents that are not the canonical import.

A duplicate can appear if the importer is run twice against DIFFERENT ledger files (each run mints
fresh object ids). The import ledger is the source of truth: every canonical document's ``object_id``
is recorded there. This maintenance tool finds published curriculum documents (those carrying a
``category`` taxonomy assignment) whose ``object_id`` is NOT in the ledger and deletes them across the
knowledge + retrieval owned tables. It is idempotent and safe to re-run.

Usage: ``python -m northstar.products.curriculum.cleanup --ledger <path> [--database-url ...] [--apply]``
Without ``--apply`` it only reports what it would delete (dry run).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import bindparam, text

from northstar.adapters.persistence_sqlalchemy.engine import (
    create_engine_from_url,
    resolve_database_url,
)

DEFAULT_TENANT = "org-bestinfopages"


def _ledger_object_ids(ledger_path: Path) -> set[str]:
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    return {str(v["object_id"]) for v in data.values() if isinstance(v, dict) and "object_id" in v}


def find_strays(engine, *, tenant: str, ledger_ids: set[str]) -> list[str]:
    """Curriculum objects (with a ``category`` taxonomy) whose id is not in the ledger."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ko.object_id
                FROM northstar_knowledge.knowledge_object ko
                JOIN northstar_knowledge.taxonomy_assignment t
                  ON t.object_id = ko.object_id AND t.scheme = 'category'
                WHERE ko.organization_id = :tenant
                """
            ),
            {"tenant": tenant},
        ).all()
    return [r.object_id for r in rows if r.object_id not in ledger_ids]


def delete_objects(engine, object_ids: list[str]) -> None:
    if not object_ids:
        return
    params = {"ids": list(object_ids)}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM northstar_retrieval.chunk_embedding
                WHERE chunk_id IN (
                  SELECT chunk_id FROM northstar_retrieval.knowledge_chunk
                  WHERE object_id IN :ids
                )
                """
            ).bindparams(bindparam("ids", expanding=True)),
            params,
        )
        conn.execute(
            text(
                "DELETE FROM northstar_retrieval.knowledge_chunk WHERE object_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            params,
        )
        for table in (
            "northstar_knowledge.block",
            "northstar_knowledge.publication",
            "northstar_knowledge.taxonomy_assignment",
            "northstar_knowledge.revision",
            "northstar_knowledge.draft",
            "northstar_knowledge.knowledge_object",
        ):
            conn.execute(
                text(f"DELETE FROM {table} WHERE object_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                params,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m northstar.products.curriculum.cleanup")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args(argv)

    engine = create_engine_from_url(resolve_database_url(args.database_url))
    ledger_ids = _ledger_object_ids(Path(args.ledger))
    strays = find_strays(engine, tenant=args.tenant, ledger_ids=ledger_ids)
    print(f"ledger object_ids: {len(ledger_ids)}")
    print(f"stray curriculum documents (not in ledger): {len(strays)}")
    for oid in strays[:50]:
        print(f"  - {oid}")
    if args.apply and strays:
        delete_objects(engine, strays)
        print(f"deleted {len(strays)} stray documents")
    elif strays:
        print("dry run: re-run with --apply to delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
