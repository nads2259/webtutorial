"""Typed content blocks + registry + content tree (LAW-06/ARCH-006, closes B-DOMAIN for content).

Canonical content is a *typed*, versioned tree of typed blocks with stable block ids — never a
giant HTML blob. Each concrete block subtype types its own payload (``attributes`` + ``content``),
so the ``data``/``attributes``/``content`` fields that the content-document / content-block JSON
schemas leave as open objects are constrained here in the domain (rule 40, B-DOMAIN).

Two deterministic projections are produced from the *same* typed tree:

* :meth:`Block.to_document_block` → the block shape inside ``content-document.schema.json``
  (``{id, type, version, data, children}``);
* :meth:`Block.to_content_block` → the standalone ``content-block.schema.json`` shape
  (``{block_id, block_type, schema_version, attributes, content, children}``).

HTML/Markdown are *further* projections (see ``adapters.projector``) and are never canonical.
This module is pure and stdlib-only (rule 10).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .errors import KnowledgeInvariantViolation, UnknownBlockType

# Block type names deliberately use only ``[a-z0-9-]`` so a single name satisfies *both* the
# content-document block-type pattern and the stricter content-block block-type pattern.
_HEADING = "heading"
_PARAGRAPH = "paragraph"
_CODE = "code"
_QUOTE = "quote"
_IMAGE = "image"
_LIST = "list"

_SCHEMA_VERSION = "1.0.0"


def _require(condition: bool, message: str, code: str = "knowledge.block.invalid") -> None:
    if not condition:
        raise KnowledgeInvariantViolation(message, code=code)


@dataclass(frozen=True, slots=True)
class Block:
    """Base typed block: a stable id plus ordered typed children.

    Concrete subtypes add their own typed payload and implement :meth:`attributes` /
    :meth:`content`. ``block_type`` and ``schema_version`` are class-level contracts so a block's
    type is intrinsic to its Python type (one authoritative shape per type).

    ``editorial_note`` is author-only editorial metadata carried on the *same* canonical block (one
    source of truth). It is surfaced by the author role projection and hidden from the learner
    projection; because plain Markdown has no way to carry it, the Markdown interchange reports any
    block that carries it as a lossy node rather than dropping it silently.
    """

    block_id: str
    children: tuple[Block, ...] = ()
    editorial_note: str | None = None

    block_type: ClassVar[str] = ""
    schema_version: ClassVar[str] = _SCHEMA_VERSION
    version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require(
            8 <= len(self.block_id) <= 128,
            "block_id must be a stable id of length 8..128",
            code="knowledge.block.id",
        )

    def attributes(self) -> dict[str, Any]:
        """Structured, typed metadata for this block (the ``attributes`` projection field)."""
        return {}

    def content(self) -> object:
        """The block's typed content payload (text, code, list items, media reference, …)."""
        return None

    def to_document_block(self) -> dict[str, Any]:
        """Project to the ``content-document.schema.json`` block shape."""
        return {
            "id": self.block_id,
            "type": self.block_type,
            "version": self.version,
            "data": {"attributes": self.attributes(), "content": self.content()},
            "children": [child.to_document_block() for child in self.children],
        }

    def to_content_block(self) -> dict[str, Any]:
        """Project to the standalone ``content-block.schema.json`` shape."""
        return {
            "block_id": self.block_id,
            "block_type": self.block_type,
            "schema_version": self.schema_version,
            "attributes": self.attributes(),
            "content": self.content(),
            "children": [child.to_content_block() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class HeadingBlock(Block):
    level: int = 1
    text: str = ""

    block_type: ClassVar[str] = _HEADING

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(1 <= self.level <= 6, "heading level must be between 1 and 6")
        _require(bool(self.text.strip()), "heading text must be non-empty")

    def attributes(self) -> dict[str, Any]:
        return {"level": self.level}

    def content(self) -> object:
        return self.text


@dataclass(frozen=True, slots=True)
class ParagraphBlock(Block):
    text: str = ""

    block_type: ClassVar[str] = _PARAGRAPH

    def content(self) -> object:
        return self.text


@dataclass(frozen=True, slots=True)
class CodeBlock(Block):
    language: str = "text"
    code: str = ""

    block_type: ClassVar[str] = _CODE

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.language.strip()), "code language must be non-empty")

    def attributes(self) -> dict[str, Any]:
        return {"language": self.language}

    def content(self) -> object:
        return self.code


@dataclass(frozen=True, slots=True)
class QuoteBlock(Block):
    text: str = ""

    block_type: ClassVar[str] = _QUOTE

    def content(self) -> object:
        return self.text


@dataclass(frozen=True, slots=True)
class ImageBlock(Block):
    src: str = ""
    alt: str = ""

    block_type: ClassVar[str] = _IMAGE

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.src.strip()), "image src must be non-empty")

    def attributes(self) -> dict[str, Any]:
        return {"alt": self.alt}

    def content(self) -> object:
        return self.src


@dataclass(frozen=True, slots=True)
class ListBlock(Block):
    ordered: bool = False
    items: tuple[str, ...] = ()

    block_type: ClassVar[str] = _LIST

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(len(self.items) >= 1, "list must have at least one item")

    def attributes(self) -> dict[str, Any]:
        return {"ordered": self.ordered}

    def content(self) -> object:
        return list(self.items)


# The authoritative block-type registry (deny-by-default: an unregistered type is rejected).
BLOCK_TYPES: dict[str, type[Block]] = {
    _HEADING: HeadingBlock,
    _PARAGRAPH: ParagraphBlock,
    _CODE: CodeBlock,
    _QUOTE: QuoteBlock,
    _IMAGE: ImageBlock,
    _LIST: ListBlock,
}


def block_from_document_dict(raw: Mapping[str, Any]) -> Block:
    """Build a typed :class:`Block` from a ``content-document`` block mapping (untrusted input).

    Deny-by-default: an unknown ``type`` raises :class:`UnknownBlockType`; a malformed payload
    raises :class:`KnowledgeInvariantViolation`. This is the single trusted parser used when
    accepting author-supplied content at the application boundary.
    """
    if not isinstance(raw, Mapping):
        raise KnowledgeInvariantViolation("block must be an object", code="knowledge.block.invalid")
    block_type = raw.get("type")
    if not isinstance(block_type, str) or block_type not in BLOCK_TYPES:
        raise UnknownBlockType(str(block_type))
    data = raw.get("data")
    if not isinstance(data, Mapping):
        raise KnowledgeInvariantViolation(
            "block data must be an object", code="knowledge.block.data"
        )
    attributes = data.get("attributes") or {}
    content = data.get("content")
    block_id = raw.get("id")
    if not isinstance(block_id, str):
        raise KnowledgeInvariantViolation("block id must be a string", code="knowledge.block.id")

    raw_children = raw.get("children") or []
    if not isinstance(raw_children, Sequence) or isinstance(raw_children, (str, bytes)):
        raise KnowledgeInvariantViolation(
            "block children must be an array", code="knowledge.block.children"
        )
    children = tuple(block_from_document_dict(child) for child in raw_children)

    return _construct(block_type, block_id, attributes, content, children)


def _construct(
    block_type: str,
    block_id: str,
    attributes: Mapping[str, Any],
    content: object,
    children: tuple[Block, ...],
) -> Block:
    """Instantiate the typed block subtype from parsed attributes/content (validates in ctor)."""
    try:
        if block_type == _HEADING:
            return HeadingBlock(
                block_id=block_id,
                children=children,
                level=int(attributes.get("level", 1)),
                text=_as_str(content),
            )
        if block_type == _PARAGRAPH:
            return ParagraphBlock(block_id=block_id, children=children, text=_as_str(content))
        if block_type == _CODE:
            return CodeBlock(
                block_id=block_id,
                children=children,
                language=_as_str(attributes.get("language", "text")),
                code=_as_str(content),
            )
        if block_type == _QUOTE:
            return QuoteBlock(block_id=block_id, children=children, text=_as_str(content))
        if block_type == _IMAGE:
            return ImageBlock(
                block_id=block_id,
                children=children,
                src=_as_str(content),
                alt=_as_str(attributes.get("alt", "")),
            )
        # _LIST
        items = content if isinstance(content, Sequence) and not isinstance(content, str) else []
        return ListBlock(
            block_id=block_id,
            children=children,
            ordered=bool(attributes.get("ordered", False)),
            items=tuple(_as_str(item) for item in items),
        )
    except (TypeError, ValueError) as exc:  # payload had the wrong Python type
        raise KnowledgeInvariantViolation(
            f"invalid payload for {block_type!r} block: {exc}", code="knowledge.block.data"
        ) from exc


def _as_str(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeInvariantViolation("expected a string payload", code="knowledge.block.data")
    return value


@dataclass(frozen=True, slots=True)
class ContentTree:
    """An ordered tree of typed blocks — the canonical content representation.

    Deterministic: :meth:`content_hash` hashes the canonical JSON projection with sorted keys, so
    the same tree always hashes identically (used as revision provenance, LAW-07).
    """

    blocks: tuple[Block, ...] = field(default_factory=tuple)

    def to_document_blocks(self) -> list[dict[str, Any]]:
        return [block.to_document_block() for block in self.blocks]

    def to_content_blocks(self) -> list[dict[str, Any]]:
        return [block.to_content_block() for block in self.blocks]

    def content_hash(self) -> str:
        """Return the ``sha256:<hex>`` provenance hash of the canonical block projection."""
        canonical = json.dumps(
            self.to_document_blocks(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    @classmethod
    def from_document_blocks(cls, raw_blocks: Sequence[Mapping[str, Any]]) -> ContentTree:
        """Parse an untrusted array of ``content-document`` block mappings into a typed tree."""
        if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
            raise KnowledgeInvariantViolation(
                "blocks must be an array", code="knowledge.document.blocks"
            )
        return cls(blocks=tuple(block_from_document_dict(raw) for raw in raw_blocks))
