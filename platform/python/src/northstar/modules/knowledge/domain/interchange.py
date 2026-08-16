"""Markdown interchange round-trip for the canonical content tree (UX-004 / EVAL-UX-004).

Markdown is an *interchange projection* of the canonical typed tree, never the source of truth
(LAW-06). This module provides a **reversible** projection:

* :func:`tree_to_markdown` renders the tree to Markdown and returns a result object that also lists
  any *lossy* nodes (constructs plain Markdown cannot represent) — they are reported explicitly,
  never silently dropped;
* :func:`markdown_to_tree` recovers the typed tree from that Markdown.

For the supported block set with no lossy constructs the round-trip is an identity that also
preserves **stable block ids**::

    markdown_to_tree(tree_to_markdown(t).markdown) == t

Stable block ids and the tree's parent/child nesting are embedded in deterministic HTML-comment
markers (``<!-- ns:block id="..." type="..." depth="N" -->``) so they survive the projection; the
human-readable content between markers is ordinary Markdown. The module is pure and stdlib-only
(rule 10): it imports no infrastructure.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .blocks import (
    Block,
    CodeBlock,
    ContentTree,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)
from .errors import KnowledgeInvariantViolation, UnknownBlockType

_FENCE = "```"

_MARKER_RE = re.compile(
    r'<!-- ns:block id="(?P<id>[^"]*)" type="(?P<type>[^"]*)" depth="(?P<depth>\d+)" -->'
)

# Markdown control characters that must be escaped so author text is never re-interpreted as
# Markdown/HTML syntax. Backslash is escaped first (and unescaped last) so the pair is a true
# inverse of each other.
_SPECIALS = "`*_[]()#!<>"


@dataclass(frozen=True, slots=True)
class LossyNode:
    """A block whose information cannot be fully represented in Markdown (reported, not dropped)."""

    block_id: str
    block_type: str
    reason: str


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    """The result of projecting a tree to Markdown: the text plus an explicit lossy report."""

    markdown: str
    lossy: tuple[LossyNode, ...] = ()

    @property
    def is_lossless(self) -> bool:
        """True when the projection dropped nothing (a faithful, reversible round-trip)."""
        return not self.lossy


def _escape(text: str) -> str:
    out = text.replace("\\", "\\\\")
    for ch in _SPECIALS:
        out = out.replace(ch, "\\" + ch)
    return out


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tree_to_markdown(tree: ContentTree) -> MarkdownDocument:
    """Project ``tree`` to Markdown, embedding stable ids/nesting and reporting lossy nodes."""
    lines: list[str] = []
    lossy: list[LossyNode] = []
    for block in tree.blocks:
        _emit_block(block, 0, lines, lossy)
    markdown = "\n".join(lines).strip("\n") + "\n"
    return MarkdownDocument(markdown=markdown, lossy=tuple(lossy))


def _emit_block(block: Block, depth: int, lines: list[str], lossy: list[LossyNode]) -> None:
    lines.append(
        f'<!-- ns:block id="{block.block_id}" type="{block.block_type}" depth="{depth}" -->'
    )
    if block.editorial_note is not None:
        lossy.append(
            LossyNode(
                block.block_id,
                block.block_type,
                "editorial_note has no Markdown representation",
            )
        )
    lines.append(_body(block, lossy))
    lines.append("")
    for child in block.children:
        _emit_block(child, depth + 1, lines, lossy)


def _body(block: Block, lossy: list[LossyNode]) -> str:
    if isinstance(block, HeadingBlock):
        level = min(max(block.level, 1), 6)
        return f"{'#' * level} {_escape(block.text)}"
    if isinstance(block, ParagraphBlock):
        return _escape(block.text)
    if isinstance(block, CodeBlock):
        if _FENCE in block.code:
            lossy.append(
                LossyNode(
                    block.block_id,
                    block.block_type,
                    "code contains a Markdown fence delimiter and cannot be fenced losslessly",
                )
            )
        return f"{_FENCE}{block.language}\n{block.code}\n{_FENCE}"
    if isinstance(block, QuoteBlock):
        return "\n".join(f"> {_escape(line)}" for line in block.text.split("\n"))
    if isinstance(block, ImageBlock):
        return f"![{_escape(block.alt)}]({block.src})"
    if isinstance(block, ListBlock):
        marker = (lambda i: f"{i + 1}.") if block.ordered else (lambda _i: "-")
        return "\n".join(f"{marker(i)} {_escape(item)}" for i, item in enumerate(block.items))
    # Unreachable for registered block types (all are handled above).
    raise UnknownBlockType(block.block_type)


def markdown_to_tree(markdown: str) -> ContentTree:
    """Recover the typed content tree from Markdown produced by :func:`tree_to_markdown`."""
    matches = list(_MARKER_RE.finditer(markdown))
    if not matches:
        raise KnowledgeInvariantViolation(
            "markdown has no ns:block markers to recover block ids from",
            code="knowledge.interchange.markers",
        )
    parsed: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip("\n").strip()
        parsed.append(
            {
                "id": match.group("id"),
                "type": match.group("type"),
                "depth": int(match.group("depth")),
                "body": body,
            }
        )
    return ContentTree(blocks=_assemble(parsed))


def _assemble(parsed: Sequence[dict[str, Any]]) -> tuple[Block, ...]:
    """Rebuild the tree (immutable, bottom-up) from a pre-order list of depth-tagged nodes."""
    roots: list[Block] = []
    stack: list[dict[str, Any]] = []

    def _close(node: dict[str, Any]) -> None:
        block = _build(node, tuple(node["children"]))
        (stack[-1]["children"] if stack else roots).append(block)

    for node in parsed:
        while stack and stack[-1]["depth"] >= node["depth"]:
            _close(stack.pop())
        stack.append({**node, "children": []})
    while stack:
        _close(stack.pop())
    return tuple(roots)


def _build(node: dict[str, Any], children: tuple[Block, ...]) -> Block:
    block_type = node["type"]
    block_id = node["id"]
    body = node["body"]
    if block_type == HeadingBlock.block_type:
        match = re.match(r"(#{1,6})\s(.*)", body, re.S)
        if match is None:
            raise KnowledgeInvariantViolation(
                "malformed heading in markdown", code="knowledge.interchange.heading"
            )
        return HeadingBlock(
            block_id=block_id,
            children=children,
            level=len(match.group(1)),
            text=_unescape(match.group(2)),
        )
    if block_type == ParagraphBlock.block_type:
        return ParagraphBlock(block_id=block_id, children=children, text=_unescape(body))
    if block_type == CodeBlock.block_type:
        code_lines = body.split("\n")
        language = code_lines[0][len(_FENCE) :] if code_lines else ""
        code = "\n".join(code_lines[1:-1])
        return CodeBlock(
            block_id=block_id, children=children, language=language or "text", code=code
        )
    if block_type == QuoteBlock.block_type:
        stripped = "\n".join(
            line[2:] if line.startswith("> ") else line for line in body.split("\n")
        )
        return QuoteBlock(block_id=block_id, children=children, text=_unescape(stripped))
    if block_type == ImageBlock.block_type:
        match = re.match(r"!\[(?P<alt>.*)\]\((?P<src>.*)\)", body, re.S)
        if match is None:
            raise KnowledgeInvariantViolation(
                "malformed image in markdown", code="knowledge.interchange.image"
            )
        return ImageBlock(
            block_id=block_id,
            children=children,
            src=match.group("src"),
            alt=_unescape(match.group("alt")),
        )
    if block_type == ListBlock.block_type:
        ordered = bool(re.match(r"\d+\.\s", body))
        items: list[str] = []
        for line in body.split("\n"):
            item = re.sub(r"^(?:\d+\.|-)\s", "", line)
            items.append(_unescape(item))
        return ListBlock(block_id=block_id, children=children, ordered=ordered, items=tuple(items))
    raise UnknownBlockType(block_type)
