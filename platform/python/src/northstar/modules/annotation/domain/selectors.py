"""W3C-inspired annotation selectors (FR-ANN-002, ``annotation.schema.json`` selector union).

A selector set anchors an annotation to an exact content target with *redundant* context so a
later revision can be remapped deterministically (docs/06 §5): a stable ``BlockSelector`` plus one
or more of ``TextQuoteSelector`` (exact + prefix/suffix), ``TextPositionSelector``,
``CodeRangeSelector`` and ``MediaFragmentSelector``. Each concrete selector is a frozen value
object that projects to the exact object shape in the ``selector`` ``oneOf`` of
``annotation.schema.json`` (validated in tests). Pure and stdlib-only (rule 10).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import InvalidSelector

_STABLE_ID_MIN = 8
_STABLE_ID_MAX = 128


@dataclass(frozen=True, slots=True)
class Selector:
    """Base marker for a typed selector (each subtype names its ``kind``)."""

    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Project to the ``annotation.schema.json`` selector object shape."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class BlockSelector(Selector):
    """A stable block-identity selector (the primary, revision-stable anchor)."""

    block_id: str = ""
    kind: str = "BlockSelector"

    def __post_init__(self) -> None:
        if not _STABLE_ID_MIN <= len(self.block_id) <= _STABLE_ID_MAX:
            raise InvalidSelector("BlockSelector.block_id must be a stable id of length 8..128")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "BlockSelector", "block_id": self.block_id}


@dataclass(frozen=True, slots=True)
class TextQuoteSelector(Selector):
    """An exact quote with optional surrounding prefix/suffix context (robust to reflow)."""

    exact: str = ""
    prefix: str | None = None
    suffix: str | None = None
    kind: str = "TextQuoteSelector"

    def __post_init__(self) -> None:
        if len(self.exact) < 1:
            raise InvalidSelector("TextQuoteSelector.exact must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "TextQuoteSelector", "exact": self.exact}
        if self.prefix is not None:
            payload["prefix"] = self.prefix
        if self.suffix is not None:
            payload["suffix"] = self.suffix
        return payload


@dataclass(frozen=True, slots=True)
class TextPositionSelector(Selector):
    """A normalized [start, end) character range in the block's text projection."""

    start: int = 0
    end: int = 1
    kind: str = "TextPositionSelector"

    def __post_init__(self) -> None:
        if self.start < 0:
            raise InvalidSelector("TextPositionSelector.start must be >= 0")
        if self.end < 1:
            raise InvalidSelector("TextPositionSelector.end must be >= 1")
        if self.end <= self.start:
            raise InvalidSelector("TextPositionSelector.end must be greater than start")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "TextPositionSelector", "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class MediaFragmentSelector(Selector):
    """A media/temporal/spatial fragment (e.g. ``t=10,20`` or ``xywh=0,0,100,100``)."""

    value: str = ""
    kind: str = "MediaFragmentSelector"

    def __post_init__(self) -> None:
        if len(self.value) < 1:
            raise InvalidSelector("MediaFragmentSelector.value must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "MediaFragmentSelector", "value": self.value}


@dataclass(frozen=True, slots=True)
class CodeRangeSelector(Selector):
    """A code line/column range selector (1-based lines; optional 1-based columns)."""

    start_line: int = 1
    end_line: int = 1
    start_column: int | None = None
    end_column: int | None = None
    kind: str = "CodeRangeSelector"

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < 1:
            raise InvalidSelector("CodeRangeSelector line numbers must be >= 1")
        if self.end_line < self.start_line:
            raise InvalidSelector("CodeRangeSelector.end_line must be >= start_line")
        for column in (self.start_column, self.end_column):
            if column is not None and column < 1:
                raise InvalidSelector("CodeRangeSelector columns must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "CodeRangeSelector",
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
        }


def _require_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise InvalidSelector(f"selector field {key!r} must be a string")
    return value


def _require_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSelector(f"selector field {key!r} must be an integer")
    return value


def _optional_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSelector(f"selector field {key!r} must be an integer or null")
    return value


def _optional_str(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidSelector(f"selector field {key!r} must be a string or null")
    return value


def parse_selector(raw: Mapping[str, Any]) -> Selector:
    """Parse one untrusted selector mapping into a typed :class:`Selector` (deny-by-default)."""
    if not isinstance(raw, Mapping):
        raise InvalidSelector("selector must be an object")
    selector_type = raw.get("type")
    if selector_type == "BlockSelector":
        return BlockSelector(block_id=_require_str(raw, "block_id"))
    if selector_type == "TextQuoteSelector":
        return TextQuoteSelector(
            exact=_require_str(raw, "exact"),
            prefix=_optional_str(raw, "prefix"),
            suffix=_optional_str(raw, "suffix"),
        )
    if selector_type == "TextPositionSelector":
        return TextPositionSelector(start=_require_int(raw, "start"), end=_require_int(raw, "end"))
    if selector_type == "MediaFragmentSelector":
        return MediaFragmentSelector(value=_require_str(raw, "value"))
    if selector_type == "CodeRangeSelector":
        return CodeRangeSelector(
            start_line=_require_int(raw, "start_line"),
            end_line=_require_int(raw, "end_line"),
            start_column=_optional_int(raw, "start_column"),
            end_column=_optional_int(raw, "end_column"),
        )
    raise InvalidSelector(f"unknown selector type {selector_type!r}")


def parse_selectors(raws: object) -> tuple[Selector, ...]:
    """Parse an untrusted selector array; require at least one selector (FR-ANN-002)."""
    if not isinstance(raws, (list, tuple)):
        raise InvalidSelector("selectors must be an array")
    selectors = tuple(parse_selector(raw) for raw in raws)
    if not selectors:
        raise InvalidSelector("a selector set must contain at least one selector")
    return selectors


def selectors_to_dicts(selectors: tuple[Selector, ...]) -> list[dict[str, Any]]:
    """Project a selector set to the canonical ``annotation.schema.json`` array form."""
    return [selector.to_dict() for selector in selectors]


def find_block_selector(selectors: tuple[Selector, ...]) -> BlockSelector | None:
    for selector in selectors:
        if isinstance(selector, BlockSelector):
            return selector
    return None


def find_text_quote_selector(selectors: tuple[Selector, ...]) -> TextQuoteSelector | None:
    for selector in selectors:
        if isinstance(selector, TextQuoteSelector):
            return selector
    return None


def find_text_position_selector(selectors: tuple[Selector, ...]) -> TextPositionSelector | None:
    for selector in selectors:
        if isinstance(selector, TextPositionSelector):
            return selector
    return None
