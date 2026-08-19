"""Pure parser: prepared lesson markdown -> typed ``knowledge`` document blocks.

Infrastructure-free (stdlib only, rule 10). Each lesson file is ``YAML frontmatter`` + a Markdown
body. The body is converted to the canonical ``content-document`` block shape
(``{id, type, version, data:{attributes, content}, children}``) the knowledge module accepts, using
only the six released block types (heading/paragraph/code/quote/image/list).

The parser is deliberately conservative: it recognises the constructs the curriculum actually uses
(ATX headings, fenced code with an info string, unordered/ordered lists, blockquotes, standalone
images) and treats everything else as paragraphs. It never emits an unknown block type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_FENCE_RE = re.compile(r"^(```+|~~~+)(.*)$")
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_IMG_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(frozen=True, slots=True)
class LessonDoc:
    """A parsed lesson ready to be seeded through knowledge capabilities."""

    lesson_id: str
    title: str
    category_id: str
    module_id: str
    document_type: str
    locale: str
    level: str
    summary: str
    tracks: tuple[str, ...]
    estimated_minutes: int | None
    blocks: tuple[dict[str, Any], ...]
    source_path: str
    frontmatter: dict[str, Any] = field(default_factory=dict)


def slugify(value: str) -> str:
    """A stable, lowercase ``[a-z0-9-]`` slug (used for block ids)."""
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "x"


def parse_lesson(path: str | Path, *, default_locale: str = "en") -> LessonDoc:
    """Parse a single lesson markdown file into a :class:`LessonDoc`."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)

    lesson_id = str(frontmatter.get("id") or p.stem)
    category_id = str(frontmatter.get("category_id") or _category_from_path(p))
    module_id = str(frontmatter.get("module_id") or "")
    title = str(frontmatter.get("title") or _first_heading(body) or p.stem)
    doc_type = _document_type(str(frontmatter.get("level", "")))
    locale = str(frontmatter.get("locale") or default_locale)
    tracks = _as_str_tuple(frontmatter.get("tracks"))
    minutes = _as_int(frontmatter.get("estimated_minutes"))

    id_prefix = slugify(lesson_id)
    blocks = markdown_to_blocks(body, id_prefix=id_prefix, drop_title=title)
    summary = _summary(blocks, minutes=minutes, level=str(frontmatter.get("level", "")))

    return LessonDoc(
        lesson_id=lesson_id,
        title=title[:300],
        category_id=category_id,
        module_id=module_id,
        document_type=doc_type,
        locale=locale,
        level=str(frontmatter.get("level", "")),
        summary=summary,
        tracks=tracks,
        estimated_minutes=minutes,
        blocks=tuple(blocks),
        source_path=str(p),
        frontmatter=frontmatter,
    )


# ---------------------------------------------------------------------------
# Markdown -> blocks
# ---------------------------------------------------------------------------


def markdown_to_blocks(
    md: str, *, id_prefix: str, drop_title: str | None = None
) -> list[dict[str, Any]]:
    """Convert a markdown body to a list of canonical ``content-document`` blocks."""
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    counter = _Counter(id_prefix)
    i = 0
    n = len(lines)
    dropped_title = False

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            i, block = _consume_fence(lines, i, fence, counter)
            if block is not None:
                blocks.append(block)
            continue

        heading = _ATX_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            i += 1
            if not text:
                continue
            # Drop a leading H1 that merely repeats the document title (title lives on the revision).
            if (
                not dropped_title
                and level == 1
                and drop_title is not None
                and _norm_title(text) == _norm_title(drop_title)
            ):
                dropped_title = True
                continue
            blocks.append(_heading_block(counter.next(), level, text))
            continue

        img = _IMG_RE.match(line)
        if img:
            blocks.append(_image_block(counter.next(), img.group("src"), img.group("alt")))
            i += 1
            continue

        if _is_table_start(lines, i):
            i, block = _consume_table(lines, i, counter)
            if block is not None:
                blocks.append(block)
            continue

        if _UL_RE.match(line) or _OL_RE.match(line):
            i, block = _consume_list(lines, i, counter)
            if block is not None:
                blocks.append(block)
            continue

        if line.lstrip().startswith(">"):
            i, block = _consume_quote(lines, i, counter)
            if block is not None:
                blocks.append(block)
            continue

        i, block = _consume_paragraph(lines, i, counter)
        if block is not None:
            blocks.append(block)

    return blocks


def _consume_fence(
    lines: list[str], i: int, fence: re.Match[str], counter: _Counter
) -> tuple[int, dict[str, Any] | None]:
    marker = fence.group(1)[0]
    info = fence.group(2).strip()
    language = (info.split() or ["text"])[0] or "text"
    runnable = "runnable" in info.split()[1:] if len(info.split()) > 1 else False
    body: list[str] = []
    i += 1
    n = len(lines)
    while i < n:
        close = _FENCE_RE.match(lines[i])
        if close and close.group(1)[0] == marker:
            i += 1
            break
        body.append(lines[i])
        i += 1
    code = "\n".join(body).rstrip("\n")
    if not code.strip():
        return i, None
    return i, _code_block(counter.next(), language, code, runnable=runnable)


def _consume_list(
    lines: list[str], i: int, counter: _Counter
) -> tuple[int, dict[str, Any] | None]:
    items: list[str] = []
    ordered = bool(_OL_RE.match(lines[i]))
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            # allow a single blank line inside a list only if the next line continues it
            nxt = lines[i + 1] if i + 1 < n else ""
            if _UL_RE.match(nxt) or _OL_RE.match(nxt):
                i += 1
                continue
            break
        m = _OL_RE.match(line) if ordered else _UL_RE.match(line)
        alt = _UL_RE.match(line) if ordered else _OL_RE.match(line)
        if m:
            items.append(m.group(1).strip())
            i += 1
            continue
        if alt:
            # a different list marker starts a new list block
            break
        # a continuation/indented line: append to the previous item
        if items and (line.startswith("  ") or line.startswith("\t")):
            items[-1] = f"{items[-1]} {line.strip()}"
            i += 1
            continue
        break
    items = [it for it in (x.strip() for x in items) if it]
    if not items:
        return i, None
    return i, _list_block(counter.next(), items, ordered=ordered)


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    return bool(_TABLE_ROW_RE.match(lines[i]) and _TABLE_SEP_RE.match(lines[i + 1]))


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _consume_table(
    lines: list[str], i: int, counter: _Counter
) -> tuple[int, dict[str, Any] | None]:
    """Turn a GFM table into a list of 'Header: value' rows (no table block type exists)."""
    headers = _split_table_row(lines[i])
    i += 2  # header + separator
    n = len(lines)
    items: list[str] = []
    while i < n and _TABLE_ROW_RE.match(lines[i]):
        cells = _split_table_row(lines[i])
        parts: list[str] = []
        for idx, cell in enumerate(cells):
            if not cell:
                continue
            header = headers[idx] if idx < len(headers) and headers[idx] else f"Col {idx + 1}"
            parts.append(f"{header}: {cell}")
        if not parts:
            i += 1
            continue
        if len(cells) >= 2 and cells[0] and cells[1]:
            rest = [c for c in cells[1:] if c]
            items.append(f"{cells[0]} — {'; '.join(rest)}")
        else:
            items.append("; ".join(parts))
        i += 1
    if not items:
        return i, None
    return i, _list_block(counter.next(), items, ordered=False)


def _norm_title(text: str) -> str:
    return re.sub(r"[\s:—–-]+", " ", text).strip().lower()


def _consume_quote(
    lines: list[str], i: int, counter: _Counter
) -> tuple[int, dict[str, Any] | None]:
    parts: list[str] = []
    n = len(lines)
    while i < n and lines[i].lstrip().startswith(">"):
        parts.append(lines[i].lstrip()[1:].strip())
        i += 1
    text = " ".join(p for p in parts if p).strip()
    if not text:
        return i, None
    return i, _quote_block(counter.next(), text)


def _consume_paragraph(
    lines: list[str], i: int, counter: _Counter
) -> tuple[int, dict[str, Any] | None]:
    parts: list[str] = []
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            break
        if (
            _FENCE_RE.match(line)
            or _ATX_RE.match(line)
            or _UL_RE.match(line)
            or _OL_RE.match(line)
            or line.lstrip().startswith(">")
            or _IMG_RE.match(line)
            or _is_table_start(lines, i)
        ):
            break
        parts.append(line.strip())
        i += 1
    text = " ".join(parts).strip()
    if not text:
        return i, None
    return i, _paragraph_block(counter.next(), text)


# ---------------------------------------------------------------------------
# Block builders (canonical content-document shape)
# ---------------------------------------------------------------------------


def _block(block_id: str, block_type: str, attributes: dict[str, Any], content: Any) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": block_type,
        "version": 1,
        "data": {"attributes": attributes, "content": content},
        "children": [],
    }


def _heading_block(block_id: str, level: int, text: str) -> dict[str, Any]:
    return _block(block_id, "heading", {"level": max(1, min(level, 6))}, text)


def _paragraph_block(block_id: str, text: str) -> dict[str, Any]:
    return _block(block_id, "paragraph", {}, text)


def _code_block(block_id: str, language: str, code: str, *, runnable: bool) -> dict[str, Any]:
    attrs: dict[str, Any] = {"language": language or "text"}
    if runnable:
        # Presentation hint for the reader/runner; ignored by the domain (attributes are open).
        attrs["runnable"] = True
    return _block(block_id, "code", attrs, code)


def _quote_block(block_id: str, text: str) -> dict[str, Any]:
    return _block(block_id, "quote", {}, text)


def _image_block(block_id: str, src: str, alt: str) -> dict[str, Any]:
    return _block(block_id, "image", {"alt": alt}, src)


def _list_block(block_id: str, items: list[str], *, ordered: bool) -> dict[str, Any]:
    return _block(block_id, "list", {"ordered": ordered}, list(items))


# ---------------------------------------------------------------------------
# Frontmatter + helpers
# ---------------------------------------------------------------------------


class _Counter:
    """Deterministic, stable block-id generator (``<prefix>-NNNN``, length >= 8)."""

    __slots__ = ("_prefix", "_i")

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._i = 0

    def next(self) -> str:
        self._i += 1
        return f"{self._prefix}-{self._i:04d}"


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    return _parse_frontmatter(match.group(1)), match.group(2)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML-subset parser: ``key: scalar`` and ``key: [a, b]`` (no nested maps)."""
    out: dict[str, Any] = {}
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        out[key] = _coerce_scalar(value)
    return out


def _coerce_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(v.strip()) for v in inner.split(",") if v.strip()]
    return _unquote(value)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _document_type(level: str) -> str:
    # Every curriculum lesson is a knowledge tutorial page (a released document_type).
    return "tutorial"


def _first_heading(body: str) -> str | None:
    for line in body.split("\n"):
        m = _ATX_RE.match(line)
        if m and m.group(2).strip():
            return m.group(2).strip()
    return None


def _category_from_path(p: Path) -> str:
    for part in p.parts:
        if re.match(r"^C\d{2}", part):
            return part.split("-", 1)[0]
    return ""


def _summary(blocks: list[dict[str, Any]], *, minutes: int | None, level: str) -> str:
    """A short summary: the first paragraph, trimmed, with a small meta suffix."""
    first = ""
    for block in blocks:
        if block["type"] == "paragraph":
            first = str(block["data"]["content"])
            break
    first = re.sub(r"\s+", " ", first).strip()
    if len(first) > 240:
        first = first[:237].rstrip() + "..."
    bits = [b for b in (level.strip(), f"{minutes} min" if minutes else "") if b]
    if bits and first:
        return f"{first}"
    return first or (" - ".join(bits))
