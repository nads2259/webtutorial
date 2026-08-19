"""Rewrite curriculum lessons into human-quality tutorials via a strong chat model.

Per lesson:
``knowledge.revision.get`` -> model (strict JSON) -> validate -> typed blocks + banner ->
``knowledge.draft.edit`` -> ``knowledge.document.submit`` -> ``knowledge.document.publish``.

Fail-safe: unusable model output leaves the lesson untouched. Resumable JSON ledger.
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

from .banners import BANNER_ATTR, BANNER_ID_PREFIX, BANNER_ROLE, banner_for_lesson

DEFAULT_TENANT = "org-bestinfopages"
ACTOR_ID = "curriculum-rewriter"

_SUBJECT_LANG: dict[str, str] = {
    "python": "Python",
    "php": "PHP",
    "java": "Java",
    "rust": "Rust",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
}

_BOILERPLATE = re.compile(
    r"(?i)\b(why it matters|in this lesson you will|learning objectives)\b"
)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(slots=True)
class RewriteStats:
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)


def _subject_language(subject: str) -> str:
    return _SUBJECT_LANG.get(subject.lower(), subject.title() or "Python")


def _prompt(
    *,
    title: str,
    kind: str,
    subject: str,
    category: str,
    module: str,
    existing_code: list[str],
) -> str:
    lang = _subject_language(subject)
    code_hint = ""
    if existing_code:
        sample = "\n\n".join(existing_code[:3])[:1200]
        code_hint = f"\nExisting code samples from the lesson (you may improve/replace them):\n{sample}\n"
    return f"""You are an expert {lang} instructor writing for Bestinfopages — a human, friendly tutorial site.

Rewrite this {kind} titled "{title}" (category {category}, module {module or 'root'}).
Subject/language: {lang}.

Return ONLY valid JSON (no markdown outside the JSON) with this exact shape:
{{
  "title": "clear human title (no PY-C00 codes)",
  "hook": "one engaging sentence — NOT 'why it matters' boilerplate",
  "sections": [
    {{"heading": "section title", "paragraphs": ["prose...", "..."]}}
  ],
  "examples": [
    {{"caption": "what this shows", "code": "runnable {lang} code", "expected_output": "exact stdout"}}
  ],
  "pitfalls": ["common mistake and fix", "..."],
  "recap": "2-3 sentence wrap-up",
  "diagram": null
}}

Rules:
- 3–6 sections with clear, conversational prose (not robotic).
- 2–4 runnable {lang} examples with realistic expected_output (no placeholders).
- pitfalls: 2–4 items.
- diagram: optional Mermaid flowchart TD string ONLY if it genuinely clarifies; otherwise null.
- NO generic concept-map diagrams. NO "why it matters" sections.
- Expand thin lessons to be genuinely helpful (minimum ~400 words across sections).
- Keep facts accurate for {lang}.
{code_hint}
Output ONLY the JSON object."""


def _extract_json(text: str) -> dict[str, Any] | None:
    body = text.strip()
    fenced = _JSON_FENCE.search(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Try to find the outermost { ... }
        start = body.find("{")
        end = body.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(body[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _validate(data: dict[str, Any], *, lang: str) -> str | None:
    title = str(data.get("title", "")).strip()
    hook = str(data.get("hook", "")).strip()
    if not title or len(title) > 300:
        return "invalid title"
    if not hook or _BOILERPLATE.search(hook):
        return "invalid hook"
    sections = data.get("sections")
    if not isinstance(sections, list) or not (2 <= len(sections) <= 8):
        return "sections count"
    word_count = 0
    for sec in sections:
        if not isinstance(sec, dict):
            return "section shape"
        heading = str(sec.get("heading", "")).strip()
        paras = sec.get("paragraphs")
        if not heading or not isinstance(paras, list) or not paras:
            return "section content"
        for p in paras:
            word_count += len(str(p).split())
    if word_count < 120:
        return "too short"
    examples = data.get("examples")
    if not isinstance(examples, list) or not (1 <= len(examples) <= 6):
        return "examples count"
    for ex in examples:
        if not isinstance(ex, dict):
            return "example shape"
        code = str(ex.get("code", "")).strip()
        out = str(ex.get("expected_output", "")).strip()
        if not code or len(code) < 8:
            return "example code"
        if not out:
            return "example output"
    pitfalls = data.get("pitfalls")
    if not isinstance(pitfalls, list) or len(pitfalls) < 1:
        return "pitfalls"
    recap = str(data.get("recap", "")).strip()
    if not recap:
        return "recap"
    diagram = data.get("diagram")
    if diagram is not None:
        d = str(diagram).strip()
        first = d.splitlines()[0].strip().lower() if d else ""
        if not (first.startswith("flowchart") or first.startswith("graph")):
            return "diagram"
    return None


def _block_id(prefix: str, kind: str, n: int) -> str:
    return f"{prefix}-{kind}-{n:02d}"[:128]


def _to_blocks(data: dict[str, Any], *, id_prefix: str, code_lang: str = "python") -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    n = 0

    hook = str(data.get("hook", "")).strip()
    if hook:
        n += 1
        blocks.append(
            {
                "id": _block_id(id_prefix, "p", n),
                "type": "paragraph",
                "version": 1,
                "data": {"attributes": {}, "content": hook},
                "children": [],
            }
        )

    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading", "")).strip()
        if heading:
            n += 1
            blocks.append(
                {
                    "id": _block_id(id_prefix, "h", n),
                    "type": "heading",
                    "version": 1,
                    "data": {"attributes": {"level": 2}, "content": heading},
                    "children": [],
                }
            )
        for para in sec.get("paragraphs") or []:
            text = str(para).strip()
            if not text:
                continue
            n += 1
            blocks.append(
                {
                    "id": _block_id(id_prefix, "p", n),
                    "type": "paragraph",
                    "version": 1,
                    "data": {"attributes": {}, "content": text},
                    "children": [],
                }
            )

    examples = data.get("examples") or []
    if examples:
        n += 1
        blocks.append(
            {
                "id": _block_id(id_prefix, "h", n),
                "type": "heading",
                "version": 1,
                "data": {"attributes": {"level": 2}, "content": "Examples"},
                "children": [],
            }
        )
        for ex in examples:
            if not isinstance(ex, dict):
                continue
            caption = str(ex.get("caption", "")).strip()
            code = str(ex.get("code", "")).strip()
            out = str(ex.get("expected_output", "")).strip()
            if caption:
                n += 1
                blocks.append(
                    {
                        "id": _block_id(id_prefix, "p", n),
                        "type": "paragraph",
                        "version": 1,
                        "data": {"attributes": {}, "content": caption},
                        "children": [],
                    }
                )
            if code:
                n += 1
                blocks.append(
                    {
                        "id": _block_id(id_prefix, "c", n),
                        "type": "code",
                        "version": 1,
                        "data": {"attributes": {"language": code_lang}, "content": code},
                        "children": [],
                    }
                )
            if out:
                n += 1
                blocks.append(
                    {
                        "id": _block_id(id_prefix, "p", n),
                        "type": "paragraph",
                        "version": 1,
                        "data": {
                            "attributes": {},
                            "content": f"**Expected output:**\n\n```\n{out}\n```",
                        },
                        "children": [],
                    }
                )

    pitfalls = data.get("pitfalls") or []
    if pitfalls:
        n += 1
        blocks.append(
            {
                "id": _block_id(id_prefix, "h", n),
                "type": "heading",
                "version": 1,
                "data": {"attributes": {"level": 2}, "content": "Common pitfalls"},
                "children": [],
            }
        )
        items = [str(p).strip() for p in pitfalls if str(p).strip()]
        if items:
            n += 1
            blocks.append(
                {
                    "id": _block_id(id_prefix, "l", n),
                    "type": "list",
                    "version": 1,
                    "data": {"attributes": {"ordered": False}, "content": items},
                    "children": [],
                }
            )

    recap = str(data.get("recap", "")).strip()
    if recap:
        n += 1
        blocks.append(
            {
                "id": _block_id(id_prefix, "h", n),
                "type": "heading",
                "version": 1,
                "data": {"attributes": {"level": 2}, "content": "Recap"},
                "children": [],
            }
        )
        n += 1
        blocks.append(
            {
                "id": _block_id(id_prefix, "p", n),
                "type": "paragraph",
                "version": 1,
                "data": {"attributes": {}, "content": recap},
                "children": [],
            }
        )

    diagram = data.get("diagram")
    if diagram:
        d = str(diagram).strip()
        if d and ("-->" in d or "---" in d):
            n += 1
            blocks.append(
                {
                    "id": _block_id(id_prefix, "m", n),
                    "type": "code",
                    "version": 1,
                    "data": {"attributes": {"language": "mermaid"}, "content": d},
                    "children": [],
                }
            )

    return blocks


def _banner_block(*, object_id: str, title: str, src: str) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]", "", object_id.lower())[:16]
    return {
        "id": f"{BANNER_ID_PREFIX}{slug}",
        "type": "image",
        "version": 1,
        "data": {"attributes": {"alt": title, BANNER_ATTR: BANNER_ROLE}, "content": src},
        "children": [],
    }


def _existing_code(blocks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if b.get("type") != "code":
            continue
        lang = str((b.get("data") or {}).get("attributes", {}).get("language", ""))
        if lang == "python":
            content = str((b.get("data") or {}).get("content", ""))
            if content.strip():
                out.append(content.strip())
    return out


class LessonRewriter:
    def __init__(
        self,
        *,
        command_bus: CommandBus,
        query_bus: QueryBus,
        tenant: str,
        ai_chat: Any,
        ai_base_url: str,
        ai_model: str,
    ) -> None:
        self._cb = command_bus
        self._qb = query_bus
        self._tenant = tenant
        self._ai = ai_chat
        self._ai_base = ai_base_url
        self._ai_model = ai_model

    def _ctx(self) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=ACTOR_ID),
            correlation_id=f"rewrite-{uuid.uuid4().hex}",
            tenant_scope=self._tenant,
        )

    def rewrite(
        self,
        *,
        object_id: str,
        revision_id: str,
        title: str,
        summary: str | None,
        visibility: str,
        kind: str,
        subject: str,
        category: str,
        module: str,
        lesson_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Returns (new_revision_id, rejection_reason). Both None if unchanged."""
        from northstar.modules.assistant.domain.model import ChatMessage

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
        code_samples = _existing_code(blocks)
        prompt = _prompt(
            title=title,
            kind=kind,
            subject=subject,
            category=category,
            module=module,
            existing_code=code_samples,
        )
        lang = _subject_language(subject)
        data: dict[str, Any] | None = None
        last_err = "json parse"
        for attempt in range(3):
            result = self._ai.complete(
                base_url=self._ai_base,
                model=self._ai_model,
                messages=(ChatMessage(role="user", content=prompt),),
                max_tokens=2800,
            )
            raw = result.text.strip()
            if not raw or raw == "(the model returned an empty response)":
                last_err = "empty response"
                continue
            data = _extract_json(raw)
            if data is None:
                last_err = "json parse"
                continue
            err = _validate(data, lang=lang)
            if err:
                last_err = err
                data = None
                continue
            break
        if data is None:
            return None, last_err

        id_prefix = re.sub(r"[^a-z0-9-]", "-", (lesson_id or object_id).lower())[:40]
        code_lang = subject.lower() if subject else "python"
        new_blocks = _to_blocks(data, id_prefix=id_prefix or "lesson", code_lang=code_lang)
        if not new_blocks:
            return None, "empty blocks"

        banner_src = banner_for_lesson(lesson_id, category, module)
        new_title = str(data.get("title", title)).strip()[:300]
        rebuilt = [_banner_block(object_id=object_id, title=new_title, src=banner_src), *new_blocks]

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
                    title=new_title,
                    visibility=visibility,
                    summary=summary,
                ),
            ),
            ctx,
        ).value
        return published.revision_id, None


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
                    "kind": terms.get("kind", "lesson"),
                    "subject": terms.get("subject", "python"),
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
    model: str | None,
) -> RewriteStats:
    from northstar.modules.assistant.adapters.openai_compatible import OpenAICompatibleChatModel
    from northstar.modules.assistant.application.config import default_store
    from northstar.products.reference.assembly import assemble_reference_product

    product = assemble_reference_product(database_url=database_url)
    store = default_store()
    # qwen3-coder-next is the most reliable for structured lesson JSON at batch scale.
    ai_model = model or "qwen3-coder-next"
    ai_base = store.base_url
    rewriter = LessonRewriter(
        command_bus=product.command_bus,
        query_bus=product.query_bus,
        tenant=tenant,
        ai_chat=OpenAICompatibleChatModel(timeout_s=120.0),
        ai_base_url=ai_base,
        ai_model=ai_model,
    )

    catalog = _catalog(database_url)
    if limit is not None:
        catalog = catalog[:limit]

    ledger = _load_ledger(ledger_path)
    stats = RewriteStats()
    total = len(catalog)
    started = time.monotonic()
    for i, row in enumerate(catalog, start=1):
        oid = row["object_id"]
        prior = ledger.get(oid)
        if prior and prior.get("done"):
            stats.skipped += 1
            continue
        try:
            new_rev, reject = rewriter.rewrite(
                object_id=oid,
                revision_id=row["revision_id"],
                title=row["title"],
                summary=row["summary"],
                visibility=row["visibility"],
                kind=row["kind"],
                subject=row["subject"],
                category=row["category"],
                module=row["module"],
                lesson_id=row.get("lesson_id"),
            )
            if new_rev is None:
                stats.rejected += 1
                ledger[oid] = {
                    "revision_id": row["revision_id"],
                    "done": False,
                    "rejected": reject,
                }
                print(f"  [reject] {oid}: {reject}", file=sys.stderr, flush=True)
            else:
                ledger[oid] = {"revision_id": new_rev, "done": True}
                stats.updated += 1
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            stats.errors.append(f"{oid}: {exc}")
            print(f"  [fail] {oid}: {exc}", file=sys.stderr, flush=True)
        if i % 10 == 0 or i == total:
            _save_ledger(ledger_path, ledger)
            rate = i / max(time.monotonic() - started, 1e-6)
            eta = (total - i) / max(rate, 1e-6)
            print(
                f"  {i}/{total} (updated={stats.updated} skipped={stats.skipped} "
                f"rejected={stats.rejected} failed={stats.failed}) "
                f"{rate:.2f}/s eta={eta / 3600:.1f}h",
                flush=True,
            )
    _save_ledger(ledger_path, ledger)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m northstar.products.curriculum.rewrite",
        description="Rewrite curriculum lessons into human-quality tutorials.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ledger", default="curriculum-rewrite-ledger.json")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--model", default=None, help="Override the active assistant model id")
    args = parser.parse_args(argv)

    stats = run(
        database_url=args.database_url,
        limit=args.limit,
        ledger_path=Path(args.ledger),
        tenant=args.tenant,
        model=args.model,
    )
    print(
        f"done: updated={stats.updated} skipped={stats.skipped} "
        f"rejected={stats.rejected} failed={stats.failed}",
        flush=True,
    )
    return 1 if stats.failed and not stats.updated else 0


if __name__ == "__main__":
    raise SystemExit(main())
