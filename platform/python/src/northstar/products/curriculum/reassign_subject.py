"""Retag existing curriculum documents from one subject onto another by category.

Used to split Python P9–P16 (C56–C95) into standalone subjects without re-importing lessons.
Updates existing ``scheme='subject'`` rows in place — never inserts a second subject term.
"""

from __future__ import annotations

import argparse

from sqlalchemy import bindparam, text

from northstar.adapters.persistence_sqlalchemy.engine import (
    create_engine_from_url,
    resolve_database_url,
)

DEFAULT_TENANT = "org-bestinfopages"

# One subject per former Python course card (P9–P16).
SPLIT_SUBJECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai-systems", tuple(f"C{n:02d}" for n in range(56, 60))),
    ("pytorch", tuple(f"C{n:02d}" for n in range(60, 65))),
    ("transformers", tuple(f"C{n:02d}" for n in range(65, 71))),
    ("cuda", tuple(f"C{n:02d}" for n in range(71, 76))),
    ("inference", tuple(f"C{n:02d}" for n in range(76, 81))),
    ("distributed", tuple(f"C{n:02d}" for n in range(81, 86))),
    ("ai-mastery", tuple(f"C{n:02d}" for n in range(86, 88))),
    ("frontier", tuple(f"C{n:02d}" for n in range(88, 96))),
)


def reassign(
    *,
    database_url: str | None,
    tenant: str,
    from_subject: str = "python",
) -> dict[str, int]:
    """UPDATE subject terms for objects whose category is in the split map."""
    engine = create_engine_from_url(resolve_database_url(database_url))
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for new_subject, categories in SPLIT_SUBJECTS:
            stmt = text(
                """
                UPDATE northstar_knowledge.taxonomy_assignment AS s
                SET term = :new_subject
                WHERE s.scheme = 'subject'
                  AND s.term = :from_subject
                  AND s.organization_id = :tenant
                  AND EXISTS (
                    SELECT 1
                    FROM northstar_knowledge.taxonomy_assignment AS c
                    WHERE c.object_id = s.object_id
                      AND c.scheme = 'category'
                      AND c.term IN :categories
                  )
                """
            ).bindparams(bindparam("categories", expanding=True))
            result = conn.execute(
                stmt,
                {
                    "new_subject": new_subject,
                    "from_subject": from_subject,
                    "tenant": tenant,
                    "categories": list(categories),
                },
            )
            counts[new_subject] = result.rowcount or 0
    return counts


def duplicate_subjects(*, database_url: str | None, tenant: str) -> int:
    """Count objects that still have more than one subject term (should be 0)."""
    engine = create_engine_from_url(resolve_database_url(database_url))
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                  SELECT object_id
                  FROM northstar_knowledge.taxonomy_assignment
                  WHERE organization_id = :tenant AND scheme = 'subject'
                  GROUP BY object_id
                  HAVING COUNT(*) > 1
                ) dup
                """
            ),
            {"tenant": tenant},
        ).scalar()
    return int(row or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m northstar.products.curriculum.reassign_subject")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--from-subject", default="python")
    args = parser.parse_args(argv)
    counts = reassign(
        database_url=args.database_url,
        tenant=args.tenant,
        from_subject=args.from_subject,
    )
    dupes = duplicate_subjects(database_url=args.database_url, tenant=args.tenant)
    total = sum(counts.values())
    for subject, n in counts.items():
        print(f"  {subject}: {n}")
    print(f"updated subject rows: {total}; objects with multiple subjects: {dupes}")
    return 1 if dupes else 0


if __name__ == "__main__":
    raise SystemExit(main())
