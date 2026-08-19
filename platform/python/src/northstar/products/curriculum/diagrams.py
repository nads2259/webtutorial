"""Add a relevant diagram to each curriculum lesson via released capabilities (no direct table writes).

Per lesson the updater runs the authoritative knowledge pipeline:
``knowledge.revision.get`` -> ``knowledge.draft.edit`` -> ``knowledge.document.submit`` ->
``knowledge.document.publish`` (LAW-04/LAW-07), minting a new immutable revision whose content tree
carries a generated Mermaid concept-map block.

Two derivation strategies share the same write path:

* ``structure`` (default, deterministic, free): a top-down Mermaid flowchart built from the lesson's
  own H2/H3 headings — always tied to the actual document, instant, and idempotent.
* ``ai`` (optional, slower): asks the configured assistant chat model for a small, relevant diagram
  (falls back to ``structure`` if the model output is unusable).

Generated blocks are tagged ``attributes.generated`` so re-runs replace them cleanly and an AI pass can
later upgrade a deterministic diagram. A JSON ledger makes the batch resumable.
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

DEFAULT_TENANT = "org-bestinfopages"
ACTOR_ID = "curriculum-diagrammer"
GEN_ATTR = "generated"
GEN_ID_PREFIX = "gen-diagram-"

_ID_RE = re.compile(r"\bPY-C\d+(?:-M\d+)?(?:-L\d+)?\b", re.IGNORECASE)
_CODE_RE = re.compile(r"\b[CM]\d{2}\b")
_MD_RE = re.compile(r"[*`_>#]+")
_BAD_RE = re.compile(r'["\[\]{}()|<>;]+')
_WS_RE = re.compile(r"\s+")

# Fallback flows for lessons without usable headings, by pedagogical kind.
_FALLBACK: dict[str, tuple[str, ...]] = {
    "lesson": ("Concept", "Worked example", "Practice", "Check understanding"),
    "exercise": ("Read prompt", "Predict output", "Run code", "Verify result"),
    "quiz": ("Recall", "Apply", "Review answers"),
    "project": ("Plan", "Build", "Test", "Reflect"),
    "assessment": ("Review scope", "Attempt", "Self-grade"),
    "overview": ("Goals", "Topics", "How to study"),
    "page": ("Intro", "Details", "Summary"),
}


def _clean(text: str, *, limit: int = 40) -> str:
    text = _ID_RE.sub("", text)
    text = _CODE_RE.sub("", text)
    text = _MD_RE.sub("", text)
    text = _BAD_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip(" -—:")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "Topic"


def _headings(blocks: list[dict[str, Any]]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for b in blocks:
        if b.get("type") != "heading":
            continue
        level = int(b.get("data", {}).get("attributes", {}).get("level", 2) or 2)
        content = str(b.get("data", {}).get("content", "")).strip()
        if content:
            out.append((level, content))
    return out


def derive_structure_mermaid(title: str, blocks: list[dict[str, Any]], kind: str) -> str:
    """Deterministic Mermaid concept map from the lesson's heading structure."""
    root = _clean(title, limit=46)
    lines = ["flowchart TD", f'  root["{root}"]']
    headings = _headings(blocks)
    edges: list[str] = []
    last_h2 = "root"
    nid = 0
    used = 0
    seen_example = False
    for level, text in headings:
        if used >= 10:
            break
        # Collapse repetitive "Example 1/2/3…" headings into a single node.
        if re.match(r"(?i)^\s*example\s+\d+", text):
            if seen_example:
                continue
            seen_example = True
            label = "Worked examples"
        else:
            label = _clean(text)
            if label.lower() == root.lower().rstrip("…"):
                continue
        nid += 1
        node = f"n{nid}"
        lines.append(f'  {node}["{label}"]')
        if level <= 2:
            edges.append(f"  root --> {node}")
            last_h2 = node
        else:
            edges.append(f"  {last_h2} --> {node}")
        used += 1
    if used < 1:
        steps = _FALLBACK.get(kind, _FALLBACK["page"])
        prev = "root"
        for i, step in enumerate(steps, start=1):
            node = f"n{i}"
            lines.append(f'  {node}["{_clean(step)}"]')
            edges.append(f"  {prev} --> {node}")
            prev = node
    return "\n".join(lines + edges)


def _sanitize_ai_mermaid(text: str) -> str | None:
    """Extract and lightly validate a Mermaid diagram from a model response."""
    fenced = re.search(r"```(?:mermaid)?\s*(.*?)```", text, re.DOTALL)
    body = (fenced.group(1) if fenced else text).strip()
    first = body.splitlines()[0].strip().lower() if body else ""
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return None
    if len(body) > 1600 or "-->" not in body:
        return None
    return body


@dataclass(slots=True)
class DiagramStats:
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class DiagramUpdater:
    """Adds/refreshes a generated diagram block through the authoritative command bus."""

    def __init__(
        self,
        *,
        command_bus: CommandBus,
        query_bus: QueryBus,
        tenant: str = DEFAULT_TENANT,
        strategy: str = "structure",
        ai_chat: Any | None = None,
        ai_base_url: str = "",
        ai_model: str = "",
    ) -> None:
        self._cb = command_bus
        self._qb = query_bus
        self._tenant = tenant
        self._strategy = strategy
        self._ai = ai_chat
        self._ai_base = ai_base_url
        self._ai_model = ai_model

    def _ctx(self) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=ACTOR_ID),
            correlation_id=f"diagram-{uuid.uuid4().hex}",
            tenant_scope=self._tenant,
        )

    def _mermaid(self, title: str, blocks: list[dict[str, Any]], kind: str) -> str:
        if self._strategy == "ai" and self._ai is not None:
            try:
                text = self._ai_summary(title, blocks)
                candidate = _sanitize_ai_mermaid(text)
                if candidate:
                    return candidate
            except Exception:  # noqa: BLE001 - fall back to deterministic on any model issue
                pass
        return derive_structure_mermaid(title, blocks, kind)

    def _ai_summary(self, title: str, blocks: list[dict[str, Any]]) -> str:
        from northstar.modules.assistant.domain.model import ChatMessage

        heads = "; ".join(t for _, t in _headings(blocks)[:12])
        prompt = (
            "Create a single small Mermaid 'flowchart TD' that helps a learner understand this Python "
            f"lesson titled '{title}'. Its sections are: {heads}. Use 4-9 nodes with short labels and "
            "clear arrows showing how the ideas connect. Output ONLY the Mermaid code in a ```mermaid "
            "fenced block, no prose."
        )
        result = self._ai.complete(
            base_url=self._ai_base,
            model=self._ai_model,
            messages=(ChatMessage(role="user", content=prompt),),
            max_tokens=400,
        )
        return result.text

    def update(self, *, object_id: str, revision_id: str, title: str, summary: str | None,
               visibility: str, kind: str) -> str:
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
        mermaid = self._mermaid(title, blocks, kind)
        slug = object_id.replace("-", "")[:16]
        new_block = {
            "id": f"{GEN_ID_PREFIX}{slug}",
            "type": "code",
            "version": 1,
            "data": {"attributes": {"language": "mermaid", GEN_ATTR: self._strategy}, "content": mermaid},
            "children": [],
        }
        rebuilt = _rebuild(blocks, new_block)

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


def _is_generated_or_generic(block: dict[str, Any]) -> bool:
    data = block.get("data", {}) or {}
    attrs = data.get("attributes", {}) or {}
    if str(block.get("id", "")).startswith(GEN_ID_PREFIX) or GEN_ATTR in attrs:
        return True
    if attrs.get("language") == "mermaid":
        content = str(data.get("content", ""))
        if "Lesson input" in content and "observation" in content:
            return True
    return False


def _rebuild(blocks: list[dict[str, Any]], new_block: dict[str, Any]) -> list[dict[str, Any]]:
    """Drop any prior generated/generic diagram, then insert the new one after the first heading."""
    kept = [b for b in blocks if not _is_generated_or_generic(b)]
    insert_at = 0
    for i, b in enumerate(kept):
        if b.get("type") == "heading":
            insert_at = i + 1
            break
    else:
        insert_at = min(1, len(kept))
    return kept[:insert_at] + [new_block] + kept[insert_at:]


# ---------------------------------------------------------------------------
# Batch CLI
# ---------------------------------------------------------------------------


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
    """Latest published revision per object, with title/summary/visibility/kind."""
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
            kind = conn.execute(
                sa.text(
                    """
                    SELECT term FROM northstar_knowledge.taxonomy_assignment
                    WHERE object_id = :oid AND scheme = 'kind' LIMIT 1
                    """
                ),
                {"oid": object_id},
            ).scalar()
            rows.append(
                {
                    "object_id": str(object_id),
                    "revision_id": str(revision_id),
                    "visibility": visibility or "organization",
                    "title": title,
                    "summary": summary,
                    "kind": kind or "lesson",
                }
            )
    return rows


def run(
    *,
    database_url: str | None,
    strategy: str,
    limit: int | None,
    ledger_path: Path,
    tenant: str,
) -> DiagramStats:
    from northstar.products.reference.assembly import assemble_reference_product

    product = assemble_reference_product(database_url=database_url)
    ai_chat = None
    ai_base = ai_model = ""
    if strategy == "ai":
        from northstar.modules.assistant.adapters.openai_compatible import OpenAICompatibleChatModel
        from northstar.modules.assistant.application.config import default_store

        store = default_store()
        ai_chat = OpenAICompatibleChatModel()
        ai_base = store.base_url
        ai_model = store.active().model

    updater = DiagramUpdater(
        command_bus=product.command_bus,
        query_bus=product.query_bus,
        tenant=tenant,
        strategy=strategy,
        ai_chat=ai_chat,
        ai_base_url=ai_base,
        ai_model=ai_model,
    )

    catalog = _catalog(database_url)
    if limit is not None:
        catalog = catalog[:limit]

    ledger = _load_ledger(ledger_path)
    stats = DiagramStats()
    total = len(catalog)
    started = time.monotonic()
    for i, row in enumerate(catalog, start=1):
        oid = row["object_id"]
        prior = ledger.get(oid)
        if prior and prior.get("strategy") == strategy and prior.get("done"):
            stats.skipped += 1
            continue
        try:
            new_rev = updater.update(
                object_id=oid,
                revision_id=row["revision_id"],
                title=row["title"],
                summary=row["summary"],
                visibility=row["visibility"],
                kind=row["kind"],
            )
            ledger[oid] = {"revision_id": new_rev, "strategy": strategy, "done": True}
            stats.updated += 1
        except Exception as exc:  # noqa: BLE001 - report and continue
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
        prog="python -m northstar.products.curriculum.diagrams",
        description="Add a relevant Mermaid diagram to each curriculum lesson.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--strategy", default="structure", choices=["structure", "ai"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ledger", default="curriculum-diagram-ledger.json")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    args = parser.parse_args(argv)

    stats = run(
        database_url=args.database_url,
        strategy=args.strategy,
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
