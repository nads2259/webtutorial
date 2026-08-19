"""Import a prepared markdown curriculum into the knowledge DB via released capabilities.

Every write is a :class:`northstar.kernel.messaging.Command` dispatched on the composed command bus
(deny-by-default policy + tamper-evident audit + single authoritative capability per action) — the
importer never touches a module's tables directly. Per lesson it runs:

``knowledge.document.create`` -> ``knowledge.document.submit`` -> ``knowledge.document.publish`` ->
``knowledge.taxonomy.assign`` (category / module / kind / order) -> ``retrieval.revision.index``.

An idempotency ledger (JSON) maps each source file to the object/revision it produced plus a source
hash, so re-runs skip unchanged lessons and can be resumed after interruption.
"""

from __future__ import annotations

import argparse
import hashlib
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
from northstar.modules.retrieval.application import capabilities as retrieval

from .parser import LessonDoc, parse_lesson, slugify
from .phases import load_phase_map

DEFAULT_TENANT = "org-bestinfopages"
IMPORT_ACTOR_ID = "curriculum-importer"

_CATEGORY_DIR_RE = re.compile(r"^C\d{2}")
_MODULE_DIR_RE = re.compile(r"^M\d{2}")
_LESSON_FILE_RE = re.compile(r"^L\d+", re.IGNORECASE)
_CHAPTER_FILE_RE = re.compile(r"^(\d{2})-.+\.md$", re.IGNORECASE)
_BOOK_SKIP_NAMES = frozenset({"AGENTS.md", "PUBLICATION_READINESS.md"})
_BOOK_PAGES: tuple[tuple[str, str], ...] = (
    ("GLOSSARY.md", "GLOSSARY"),
    ("LABS_AND_EXERCISES.md", "LABS"),
    ("INTERVIEW_GUIDE.md", "INTERVIEW"),
    ("SOURCES.md", "SOURCES"),
    ("CHANGELOG.md", "CHANGELOG"),
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _skip_book_file(name: str) -> bool:
    """True for operational / concatenated files that must not become public lessons."""
    if name in _BOOK_SKIP_NAMES:
        return True
    upper = name.upper()
    return upper.endswith("_COMPLETE.MD") or "FULL_COURSE_COMPLETE" in upper


@dataclass(frozen=True, slots=True)
class SourceDoc:
    """One markdown file to import, with its place in the category -> module -> file order."""

    path: Path
    category_id: str
    module_dir: str
    kind: str
    order: int
    lesson_id: str | None = None


@dataclass(slots=True)
class ImportStats:
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def discover(root: str | Path, *, categories: tuple[str, ...] | None = None) -> list[SourceDoc]:
    """Walk ``<root>/tutorials`` and return every importable markdown doc in deterministic order."""
    base = Path(root)
    tutorials = base / "tutorials" if (base / "tutorials").is_dir() else base
    docs: list[SourceDoc] = []
    order = 0
    for cat_dir in sorted(p for p in tutorials.iterdir() if p.is_dir()):
        if not _CATEGORY_DIR_RE.match(cat_dir.name):
            continue
        category_id = cat_dir.name.split("-", 1)[0]
        if categories and category_id not in categories:
            continue
        # Category-level docs first (overview / project / assessment).
        for f in sorted(cat_dir.glob("*.md")):
            if f.name == "AGENTS.md":
                continue
            order += 1
            docs.append(SourceDoc(f, category_id, "", _kind_of(f.name), order))
        # Then each module in order, with its lessons.
        for mod_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            if not _MODULE_DIR_RE.match(mod_dir.name):
                continue
            for f in sorted(mod_dir.glob("*.md")):
                if f.name == "AGENTS.md":
                    continue
                order += 1
                docs.append(SourceDoc(f, category_id, mod_dir.name, _kind_of(f.name), order))
    return docs


def _book_id_prefix(category_id: str) -> str:
    """Keep LG00 lesson ids as ``LG-L01``; later book categories use ``{category}-L01``."""
    return "LG" if category_id == "LG00" else category_id


def discover_book(
    root: str | Path,
    *,
    category_id: str = "LG00",
    chapter_kind: str = "lesson",
    id_prefix: str | None = None,
) -> list[SourceDoc]:
    """Discover a flat numbered markdown book (README + NN-*.md + companion pages).

    Only the course folder is walked (not ``remaining-work/``). Concatenated COMPLETE files and
    publication-readiness trackers are skipped.
    """
    base = Path(root)
    prefix = id_prefix or _book_id_prefix(category_id)
    docs: list[SourceDoc] = []
    order = 0
    readme = base / "README.md"
    if readme.is_file():
        order += 1
        docs.append(SourceDoc(readme, category_id, "", "overview", order, f"{prefix}-OVERVIEW"))
    for path in sorted(p for p in base.glob("*.md") if p.is_file()):
        if _skip_book_file(path.name):
            continue
        match = _CHAPTER_FILE_RE.match(path.name)
        if not match:
            continue
        number = match.group(1)
        stem = path.stem
        rest = stem.split("-", 1)[1] if "-" in stem else stem
        order += 1
        docs.append(
            SourceDoc(
                path,
                category_id,
                f"M{number}-{rest}",
                chapter_kind,
                order,
                f"{prefix}-L{number}",
            )
        )
    for filename, suffix in _BOOK_PAGES:
        page = base / filename
        if page.is_file():
            order += 1
            docs.append(SourceDoc(page, category_id, "", "page", order, f"{prefix}-{suffix}"))
    return docs


def _kind_of(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    if _LESSON_FILE_RE.match(stem):
        return "lesson"
    upper = stem.upper()
    if upper == "README":
        return "overview"
    if upper == "QUIZ":
        return "quiz"
    if upper.startswith("EXERCISE"):
        return "exercise"
    if upper == "CATEGORY-PROJECT":
        return "project"
    if upper == "CATEGORY-ASSESSMENT":
        return "assessment"
    return "page"


class CurriculumImporter:
    """Seeds curriculum documents through the authoritative command bus (LAW-04)."""

    def __init__(
        self,
        *,
        command_bus: CommandBus,
        query_bus: QueryBus | None = None,
        tenant: str = DEFAULT_TENANT,
        actor_id: str = IMPORT_ACTOR_ID,
        visibility: str = "organization",
        index: bool = True,
        phase_map: dict[str, tuple[str, str]] | None = None,
        subject: str = "python",
    ) -> None:
        self._bus = command_bus
        self._queries = query_bus
        self._tenant = tenant
        self._actor_id = actor_id
        self._visibility = visibility
        self._index = index
        self._phase_map = phase_map or {}
        self._subject = subject

    def _ctx(self) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=self._actor_id),
            correlation_id=f"import-{uuid.uuid4().hex}",
            tenant_scope=self._tenant,
        )

    def _run(self, capability: str, version: str, payload: object) -> Any:
        result = self._bus.dispatch(
            Command(capability=capability, version=version, payload=payload), self._ctx()
        )
        return result.value

    def import_doc(
        self, src: SourceDoc, *, existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Import a single source doc end-to-end; return a ledger entry.

        When ``existing`` has an ``object_id``, the document is updated in place (edit → submit →
        publish) instead of creating a duplicate.
        """
        lesson = parse_lesson(src.path)
        lesson_id = src.lesson_id or lesson.lesson_id
        blocks = lesson.blocks or (_fallback_block(lesson),)
        object_id = str(existing["object_id"]) if existing and existing.get("object_id") else None

        if object_id:
            self._run(
                knowledge.CAP_EDIT_DRAFT,
                knowledge.CAP_VERSION,
                knowledge.EditDraftCommand(object_id=object_id, blocks=tuple(blocks)),
            )
        else:
            created = self._run(
                knowledge.CAP_CREATE_DOCUMENT,
                knowledge.CAP_VERSION,
                knowledge.CreateDocumentCommand(
                    document_type=lesson.document_type,
                    locale=lesson.locale,
                    title=lesson.title,
                    blocks=tuple(blocks),
                    summary=lesson.summary or None,
                ),
            )
            object_id = created.object_id

        self._run(
            knowledge.CAP_SUBMIT_FOR_REVIEW,
            knowledge.CAP_VERSION,
            knowledge.SubmitForReviewCommand(object_id=object_id),
        )
        published = self._run(
            knowledge.CAP_PUBLISH_DOCUMENT,
            knowledge.CAP_VERSION,
            knowledge.PublishDocumentCommand(
                object_id=object_id,
                title=lesson.title,
                visibility=self._visibility,
                summary=lesson.summary or None,
            ),
        )
        revision_id = published.revision_id

        if not existing:
            for scheme, term in _taxonomy_terms(
                lesson, src, self._phase_map, self._subject, lesson_id=lesson_id
            ):
                self._run(
                    knowledge.CAP_ASSIGN_TAXONOMY,
                    knowledge.CAP_VERSION,
                    knowledge.AssignTaxonomyCommand(object_id=object_id, scheme=scheme, term=term),
                )

        if self._index:
            passages = _passages(blocks)
            if passages:
                self._run(
                    retrieval.CAP_INDEX_REVISION,
                    retrieval.CAP_VERSION,
                    retrieval.IndexRevisionCommand(
                        object_id=object_id,
                        revision_id=revision_id,
                        visibility=self._visibility,
                        passages=passages,
                        locale=lesson.locale,
                    ),
                )

        return {
            "object_id": object_id,
            "revision_id": revision_id,
            "content_hash": published.content_hash,
            "title": lesson.title,
            "summary": lesson.summary or None,
            "category_id": src.category_id,
            "module": src.module_dir,
            "kind": src.kind,
            "order": src.order,
            "lesson_id": lesson_id,
        }


def _taxonomy_terms(
    lesson: LessonDoc,
    src: SourceDoc,
    phase_map: dict[str, tuple[str, str]],
    subject: str = "python",
    lesson_id: str | None = None,
) -> list[tuple[str, str]]:
    # The top-level Subject/language dimension (Python today; PHP/Java/Rust/... later).
    terms: list[tuple[str, str]] = [
        ("subject", subject),
        ("category", src.category_id),
        ("kind", src.kind),
    ]
    phase = phase_map.get(src.category_id)
    if phase:
        terms.append(("phase", phase[0]))
        terms.append(("phase_title", phase[1]))
    if src.module_dir:
        terms.append(("module", src.module_dir))
    if lesson.level:
        terms.append(("level", lesson.level))
    for track in lesson.tracks:
        terms.append(("track", track))
    # A stable, sortable ordering key for the sidebar/pager.
    terms.append(("order", f"{src.order:06d}"))
    terms.append(("lesson", lesson_id or lesson.lesson_id))
    return terms


def _passages(blocks: tuple[dict[str, Any], ...]) -> tuple[retrieval.PassageInput, ...]:
    out: list[retrieval.PassageInput] = []
    ordinal = 0
    for block in blocks:
        text = _block_text(block)
        if not text.strip():
            continue
        out.append(
            retrieval.PassageInput(block_id=str(block["id"]), ordinal=ordinal, text=text[:8000])
        )
        ordinal += 1
    return tuple(out)


def _block_text(block: dict[str, Any]) -> str:
    content = block.get("data", {}).get("content")
    if isinstance(content, list):
        return " ".join(str(x) for x in content)
    if isinstance(content, str):
        return content
    return ""


def _fallback_block(lesson: LessonDoc) -> dict[str, Any]:
    return {
        "id": f"{slugify(lesson.lesson_id)}-0001",
        "type": "paragraph",
        "version": 1,
        "data": {"attributes": {}, "content": lesson.title},
        "children": [],
    }


# ---------------------------------------------------------------------------
# Ledger + CLI
# ---------------------------------------------------------------------------


def _rewrite_href(raw: str, mapping: dict[str, str]) -> str | None:
    path = raw.split("#", 1)[0].strip()
    if path.startswith("http://") or path.startswith("https://") or path.startswith("/"):
        return None
    return mapping.get(Path(path).name)


def _rewrite_text(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        dest = _rewrite_href(match.group(2), mapping)
        if dest is None:
            return match.group(0)
        changed = True
        return f"[{match.group(1)}]({dest})"

    return _MD_LINK_RE.sub(repl, text), changed


def _rewrite_blocks(blocks: list[dict[str, Any]], mapping: dict[str, str]) -> tuple[list[dict[str, Any]], bool]:
    changed = False
    out: list[dict[str, Any]] = []
    for block in blocks:
        clone = dict(block)
        data = dict(clone.get("data") or {})
        content = data.get("content")
        if isinstance(content, str):
            new_text, hit = _rewrite_text(content, mapping)
            if hit:
                data["content"] = new_text
                clone["data"] = data
                changed = True
        elif isinstance(content, list):
            new_items: list[Any] = []
            list_hit = False
            for item in content:
                if isinstance(item, str):
                    new_item, hit = _rewrite_text(item, mapping)
                    new_items.append(new_item)
                    list_hit = list_hit or hit
                else:
                    new_items.append(item)
            if list_hit:
                data["content"] = new_items
                clone["data"] = data
                changed = True
        out.append(clone)
    return out, changed


def _filename_hrefs(ledger: dict[str, Any], *, category_id: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        name = Path(str(entry.get("source_path") or key)).name
        cat = str(entry.get("category_id") or category_id)
        kind = str(entry.get("kind") or "")
        revision_id = entry.get("revision_id")
        if kind == "overview":
            mapping[name] = f"/c/{cat}"
        elif revision_id:
            mapping[name] = f"/l/{cat}/{revision_id}"
    return mapping


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def run_import(
    *,
    root: str,
    database_url: str | None,
    categories: tuple[str, ...] | None,
    limit: int | None,
    ledger_path: Path,
    tenant: str,
    visibility: str,
    index: bool,
    subject: str = "python",
    layout: str = "tree",
    category_id: str = "LG00",
    phase_id: str = "P0",
    phase_title: str = "LangGraph Orchestration",
    chapter_kind: str = "lesson",
    id_prefix: str | None = None,
) -> ImportStats:
    # Imported lazily so `--help` and unit tests do not require a database.
    from northstar.products.reference.assembly import assemble_reference_product

    product = assemble_reference_product(database_url=database_url)
    if layout == "book":
        phase_map = {category_id: (phase_id, phase_title)}
        docs = discover_book(
            root,
            category_id=category_id,
            chapter_kind=chapter_kind,
            id_prefix=id_prefix,
        )
    else:
        try:
            phase_map = load_phase_map(root)
        except (OSError, ValueError):
            phase_map = {}
        docs = discover(root, categories=categories)
    importer = CurriculumImporter(
        command_bus=product.command_bus,
        query_bus=product.query_bus,
        tenant=tenant,
        visibility=visibility,
        index=index,
        phase_map=phase_map,
        subject=subject,
    )

    if limit is not None:
        docs = docs[:limit]

    ledger = _load_ledger(ledger_path)
    stats = ImportStats()
    total = len(docs)
    started = time.monotonic()
    for i, src in enumerate(docs, start=1):
        key = str(src.path)
        digest = _source_hash(src.path)
        prior = ledger.get(key)
        if prior and prior.get("source_hash") == digest:
            stats.skipped += 1
            continue
        try:
            entry = importer.import_doc(
                src, existing=prior if isinstance(prior, dict) else None
            )
            entry["source_hash"] = digest
            entry["source_path"] = key
            ledger[key] = entry
            stats.imported += 1
        except Exception as exc:  # noqa: BLE001 - importer reports and continues
            stats.failed += 1
            stats.errors.append(f"{src.path}: {exc}")
            print(f"  [fail] {src.path}: {exc}", file=sys.stderr)
        if i % 25 == 0 or i == total:
            _save_ledger(ledger_path, ledger)
            rate = i / max(time.monotonic() - started, 1e-6)
            print(
                f"  {i}/{total} (imported={stats.imported} skipped={stats.skipped} "
                f"failed={stats.failed}) {rate:.1f}/s",
                flush=True,
            )
    _save_ledger(ledger_path, ledger)
    if layout == "book" and ledger:
        rewritten = _rewrite_book_links(importer, ledger, category_id=category_id)
        if rewritten:
            _save_ledger(ledger_path, ledger)
            print(f"  rewrote relative links in {rewritten} docs", flush=True)
    return stats


def _rewrite_book_links(
    importer: CurriculumImporter, ledger: dict[str, Any], *, category_id: str
) -> int:
    """Second pass: turn sibling `.md` links into `/l/<cat>/<revision>` (or `/c/` for overview)."""
    if importer._queries is None:
        return 0
    mapping = _filename_hrefs(ledger, category_id=category_id)
    rewritten = 0
    for key, entry in list(ledger.items()):
        if not isinstance(entry, dict):
            continue
        object_id = entry.get("object_id")
        revision_id = entry.get("revision_id")
        title = entry.get("title")
        if not object_id or not revision_id or not title:
            continue
        ctx = importer._ctx()
        rev = importer._queries.dispatch(
            Query(
                capability=knowledge.CAP_GET_REVISION,
                version=knowledge.CAP_VERSION,
                parameters=knowledge.GetRevisionQuery(revision_id=str(revision_id)),
            ),
            ctx,
        ).value
        blocks, changed = _rewrite_blocks([dict(b) for b in rev.blocks], mapping)
        if not changed:
            continue
        importer._run(
            knowledge.CAP_EDIT_DRAFT,
            knowledge.CAP_VERSION,
            knowledge.EditDraftCommand(object_id=str(object_id), blocks=tuple(blocks)),
        )
        importer._run(
            knowledge.CAP_SUBMIT_FOR_REVIEW,
            knowledge.CAP_VERSION,
            knowledge.SubmitForReviewCommand(object_id=str(object_id)),
        )
        published = importer._run(
            knowledge.CAP_PUBLISH_DOCUMENT,
            knowledge.CAP_VERSION,
            knowledge.PublishDocumentCommand(
                object_id=str(object_id),
                title=str(title),
                visibility=importer._visibility,
                summary=entry.get("summary"),
            ),
        )
        entry["revision_id"] = published.revision_id
        rewritten += 1
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m northstar.products.curriculum",
        description="Import a prepared markdown curriculum into the knowledge DB.",
    )
    parser.add_argument("--root", required=True, help="curriculum root (contains tutorials/)")
    parser.add_argument("--database-url", default=None, help="target DATABASE_URL")
    parser.add_argument(
        "--categories", default=None, help="comma-separated category ids (e.g. C00,C01)"
    )
    parser.add_argument("--limit", type=int, default=None, help="max docs to import")
    parser.add_argument(
        "--ledger", default="curriculum-import-ledger.json", help="idempotency ledger path"
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument(
        "--visibility", default="organization", choices=["public", "organization", "private"]
    )
    parser.add_argument("--no-index", action="store_true", help="skip retrieval indexing")
    parser.add_argument("--subject", default="python", help="subject/language id (e.g. python, php)")
    parser.add_argument(
        "--layout",
        default="tree",
        choices=["tree", "book"],
        help="tree = Cxx/Mxx curriculum; book = flat numbered markdown chapters",
    )
    parser.add_argument("--category", default="LG00", help="category id for --layout book")
    parser.add_argument("--phase-id", default="P0", help="phase id for --layout book")
    parser.add_argument(
        "--phase-title",
        default="LangGraph Orchestration",
        help="phase/course title for --layout book",
    )
    parser.add_argument(
        "--chapter-kind",
        default="lesson",
        help="taxonomy kind for numbered NN-*.md files (lesson or page)",
    )
    parser.add_argument(
        "--id-prefix",
        default=None,
        help="taxonomy id prefix (default LG for LG00, otherwise the category id)",
    )
    args = parser.parse_args(argv)

    categories = (
        tuple(c.strip() for c in args.categories.split(",") if c.strip())
        if args.categories
        else None
    )
    stats = run_import(
        root=args.root,
        database_url=args.database_url,
        categories=categories,
        limit=args.limit,
        ledger_path=Path(args.ledger),
        tenant=args.tenant,
        visibility=args.visibility,
        index=not args.no_index,
        subject=args.subject,
        layout=args.layout,
        category_id=args.category,
        phase_id=args.phase_id,
        phase_title=args.phase_title,
        chapter_kind=args.chapter_kind,
        id_prefix=args.id_prefix,
    )
    print(
        f"done: imported={stats.imported} skipped={stats.skipped} failed={stats.failed}",
        flush=True,
    )
    return 1 if stats.failed and not stats.imported else 0


if __name__ == "__main__":
    raise SystemExit(main())
