"""Course/phase structure for the curriculum (the ``manifests/course-manifest.json`` grouping).

The source curriculum is one bundle organised into ~17 phases (P0..P16) — effectively multiple
courses — each grouping a run of categories (C00..C95). The reference importer flattened categories;
this module restores the phase grouping.

It provides:
* :func:`load_phase_map` — read the manifest and return ``category_id -> (phase_id, phase_title)``;
* :func:`backfill` — idempotently assign ``phase`` + ``phase_title`` taxonomy to every already-
  imported document (fast bulk SQL), so the UI can group categories into courses without a re-import;
* a CLI (``python -m northstar.products.curriculum.phases``).

The importer also consumes :func:`load_phase_map` so fresh imports carry the phase taxonomy natively.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from northstar.adapters.persistence_sqlalchemy.engine import (
    create_engine_from_url,
    resolve_database_url,
)

DEFAULT_TENANT = "org-bestinfopages"


def _manifest_path(root: str | Path) -> Path:
    p = Path(root)
    cand = p / "manifests" / "course-manifest.json"
    return cand if cand.exists() else p


def load_phase_map(root: str | Path) -> dict[str, tuple[str, str]]:
    """Return ``category_id -> (phase_id, phase_title)`` from the course manifest."""
    manifest = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    out: dict[str, tuple[str, str]] = {}
    for cat in manifest.get("categories", []):
        cid = str(cat.get("id", ""))
        phase = str(cat.get("phase", ""))
        title = str(cat.get("phase_title", ""))
        if cid and phase:
            out[cid] = (phase, title)
    return out


def backfill(*, database_url: str | None, tenant: str, root: str | Path) -> dict[str, int]:
    """Idempotently assign ``phase`` + ``phase_title`` taxonomy to every imported document."""
    phase_map = load_phase_map(root)
    engine = create_engine_from_url(resolve_database_url(database_url))
    assigned = {"phase": 0, "phase_title": 0}
    with engine.begin() as conn:
        for cat_id, (phase_id, phase_title) in phase_map.items():
            for scheme, term in (("phase", phase_id), ("phase_title", phase_title)):
                result = conn.execute(
                    text(
                        """
                        INSERT INTO northstar_knowledge.taxonomy_assignment
                          (assignment_id, object_id, organization_id, scheme, term)
                        SELECT gen_random_uuid()::text, ko.object_id, ko.organization_id,
                               :scheme, :term
                        FROM northstar_knowledge.knowledge_object ko
                        JOIN northstar_knowledge.taxonomy_assignment c
                          ON c.object_id = ko.object_id AND c.scheme = 'category' AND c.term = :cat
                        WHERE ko.organization_id = :tenant
                          AND NOT EXISTS (
                            SELECT 1 FROM northstar_knowledge.taxonomy_assignment e
                            WHERE e.object_id = ko.object_id AND e.scheme = :scheme
                          )
                        """
                    ),
                    {
                        "scheme": scheme,
                        "term": term,
                        "cat": cat_id,
                        "tenant": tenant,
                    },
                )
                assigned[scheme] += result.rowcount or 0
    return assigned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m northstar.products.curriculum.phases")
    parser.add_argument("--root", required=True, help="curriculum root (contains manifests/)")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    args = parser.parse_args(argv)
    counts = backfill(database_url=args.database_url, tenant=args.tenant, root=args.root)
    print(f"assigned phase rows: {counts['phase']}, phase_title rows: {counts['phase_title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
