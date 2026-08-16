"""Research typed content blocks (figures, tables, literature-review sections).

These REUSE the shared knowledge typed-block model (LAW-04/LAW-06): every research block subclasses
the knowledge ``Block`` base, so it inherits the stable-block-id contract
and the canonical ``to_document_block`` projection (HTML/Markdown remain further projections). The
research module adds only the discipline-specific typed structures docs/37 §1 requires that the
knowledge core does not itself define (figure, table) plus the literature-review section, each with
its own validated payload. Pure and stdlib-only (rule 10); no infrastructure is reachable.

Accessibility is an invariant, not an afterthought (LAW-08): a :class:`FigureBlock` REQUIRES
alt text so a figure is never published without a text alternative (WCAG 2.2 AA).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from northstar.modules.knowledge.domain.blocks import Block

from .errors import ResearchInvariantViolation

FIGURE = "research-figure"
TABLE = "research-table"
LITERATURE_REVIEW_SECTION = "literature-review-section"

RESEARCH_BLOCK_KINDS: tuple[str, ...] = (FIGURE, TABLE, LITERATURE_REVIEW_SECTION)


def _require(condition: bool, message: str, code: str = "research.block.invalid") -> None:
    if not condition:
        raise ResearchInvariantViolation(message, code=code)


@dataclass(frozen=True, slots=True)
class FigureBlock(Block):
    """A figure: an image reference with REQUIRED alt text (a11y) and a caption (docs/37 §1)."""

    src: str = ""
    alt: str = ""
    caption: str = ""

    block_type: ClassVar[str] = FIGURE

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.src.strip()), "figure src (image reference) is required")
        _require(
            bool(self.alt.strip()),
            "figure requires alt text (accessibility, WCAG 2.2 AA)",
            code="research.block.figure.alt",
        )
        _require(bool(self.caption.strip()), "figure requires a caption")

    def attributes(self) -> dict[str, Any]:
        return {"alt": self.alt, "caption": self.caption}

    def content(self) -> object:
        return self.src


@dataclass(frozen=True, slots=True)
class TableBlock(Block):
    """A table: a caption, >=1 column header and rectangular rows matching the header count."""

    caption: str = ""
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    block_type: ClassVar[str] = TABLE

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.caption.strip()), "table requires a caption")
        _require(
            len(self.headers) >= 1,
            "table requires at least one column header",
            code="research.block.table.headers",
        )
        for index, row in enumerate(self.rows):
            _require(
                len(row) == len(self.headers),
                f"table row {index} has {len(row)} cells but the table has "
                f"{len(self.headers)} columns",
                code="research.block.table.rows",
            )

    def attributes(self) -> dict[str, Any]:
        return {"caption": self.caption, "headers": list(self.headers)}

    def content(self) -> object:
        return [list(row) for row in self.rows]


@dataclass(frozen=True, slots=True)
class LiteratureReviewSection(Block):
    """A literature-review section: a titled prose summary that may anchor citation ids."""

    title: str = ""
    summary: str = ""
    citation_ids: tuple[str, ...] = ()

    block_type: ClassVar[str] = LITERATURE_REVIEW_SECTION

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.title.strip()), "literature-review section requires a title")
        _require(
            bool(self.summary.strip()),
            "literature-review section requires a summary",
            code="research.block.litreview.summary",
        )

    def attributes(self) -> dict[str, Any]:
        return {"title": self.title, "citation_ids": list(self.citation_ids)}

    def content(self) -> object:
        return self.summary


def build_research_block(kind: str, *, block_id: str, payload: Mapping[str, Any]) -> Block:
    """Build a validated research typed block from ``kind`` + an untrusted ``payload`` mapping.

    Deny-by-default: an unknown ``kind`` is rejected; a malformed payload raises the block's own
    :class:`ResearchInvariantViolation`. This is the single trusted parser for author-supplied
    research blocks at the application boundary (rule 40).
    """
    if kind == FIGURE:
        return FigureBlock(
            block_id=block_id,
            src=_as_str(payload.get("src")),
            alt=_as_str(payload.get("alt")),
            caption=_as_str(payload.get("caption")),
        )
    if kind == TABLE:
        return TableBlock(
            block_id=block_id,
            caption=_as_str(payload.get("caption")),
            headers=tuple(_as_str(h) for h in _as_sequence(payload.get("headers"))),
            rows=tuple(
                tuple(_as_str(cell) for cell in _as_sequence(row))
                for row in _as_sequence(payload.get("rows"))
            ),
        )
    if kind == LITERATURE_REVIEW_SECTION:
        return LiteratureReviewSection(
            block_id=block_id,
            title=_as_str(payload.get("title")),
            summary=_as_str(payload.get("summary")),
            citation_ids=tuple(_as_str(c) for c in _as_sequence(payload.get("citation_ids"))),
        )
    raise ResearchInvariantViolation(
        f"unknown research block kind {kind!r}", code="research.block.kind"
    )


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _as_sequence(value: object) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    raise ResearchInvariantViolation(
        "expected an array payload for a research block", code="research.block.data"
    )
