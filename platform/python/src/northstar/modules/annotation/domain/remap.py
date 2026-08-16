"""Deterministic revision remapping (FR-ANN-004, docs/06 §5).

When a new revision of the target content is published, an annotation's anchor may need to move.
This module is a **pure, deterministic** strategy that NEVER overwrites the original target and
NEVER silently attaches to the nearest text: it evaluates evidence in a fixed order and either
returns a confident mapped target or routes the annotation to review.

Ordered evidence tiers (docs/06 §5 verbatim order):

1. ``BLOCK_IDENTITY`` — the stable block id still exists in the new revision.
2. ``BLOCK_ANCESTRY`` — the original block id survives as an ancestor of exactly one new block.
3. ``CONTENT_FINGERPRINT`` — a new block has the same structural/content fingerprint.
4. ``EXACT_QUOTE`` — exactly one new block contains the exact quoted text.
5. ``PREFIX_SUFFIX`` — exactly one new block contains ``prefix + exact + suffix``.
6. ``NORMALIZED_POSITION`` — the normalized character range falls inside exactly one new block.

Each tier resolves only when it identifies a UNIQUE target block; ambiguity (multiple candidates)
defers to the next tier. If no tier resolves, the result is ``NEEDS_REVIEW`` with a review reason
(``orphaned`` when no evidence matched at all, ``ambiguous`` when evidence was non-unique). Pure and
stdlib-only (rule 10).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .selectors import (
    Selector,
    find_block_selector,
    find_text_position_selector,
    find_text_quote_selector,
)


class RemapStrategy(StrEnum):
    """The deterministic evidence tier that produced (or failed to produce) a mapping."""

    BLOCK_IDENTITY = "block_identity"
    BLOCK_ANCESTRY = "block_ancestry"
    CONTENT_FINGERPRINT = "content_fingerprint"
    EXACT_QUOTE = "exact_quote"
    PREFIX_SUFFIX = "prefix_suffix"
    NORMALIZED_POSITION = "normalized_position"
    NEEDS_REVIEW = "needs_review"


class ReviewReason(StrEnum):
    """Why a remap was routed to human review instead of being applied."""

    ORPHANED = "orphaned"
    AMBIGUOUS = "ambiguous"


_CONFIDENCE: dict[RemapStrategy, float] = {
    RemapStrategy.BLOCK_IDENTITY: 1.0,
    RemapStrategy.BLOCK_ANCESTRY: 0.9,
    RemapStrategy.CONTENT_FINGERPRINT: 0.85,
    RemapStrategy.EXACT_QUOTE: 0.75,
    RemapStrategy.PREFIX_SUFFIX: 0.6,
    RemapStrategy.NORMALIZED_POSITION: 0.5,
    RemapStrategy.NEEDS_REVIEW: 0.0,
}


@dataclass(frozen=True, slots=True)
class BlockSnapshot:
    """A pure, comparable view of one block in a revision (for remap evidence)."""

    block_id: str
    text: str
    fingerprint: str
    ancestry: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    """The set of block snapshots for a revision, in document order (pure)."""

    revision_id: str
    blocks: tuple[BlockSnapshot, ...] = field(default_factory=tuple)

    def block(self, block_id: str) -> BlockSnapshot | None:
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        return None


@dataclass(frozen=True, slots=True)
class RemapResult:
    """The deterministic outcome of a remap attempt (mapped or routed to review).

    The original target is *never* part of this result; it stays on the annotation forever
    (FR-ANN-003). ``target_revision_id`` and ``matched_block_id`` are populated only when
    ``strategy`` is a mapping tier; a review outcome carries a ``review_reason`` instead.
    """

    source_revision_id: str
    strategy: RemapStrategy
    confidence: float
    target_revision_id: str | None = None
    matched_block_id: str | None = None
    review_reason: ReviewReason | None = None
    evidence: str = ""

    @property
    def mapped(self) -> bool:
        return self.strategy is not RemapStrategy.NEEDS_REVIEW

    @property
    def needs_review(self) -> bool:
        return not self.mapped

    def to_dict(self) -> dict[str, object]:
        """Serialise for the ``target.current_remap`` provenance object (schema-open)."""
        return {
            "source_revision_id": self.source_revision_id,
            "target_revision_id": self.target_revision_id,
            "strategy": self.strategy.value,
            "confidence": self.confidence,
            "matched_block_id": self.matched_block_id,
            "review_reason": self.review_reason.value if self.review_reason else None,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RemapResult:
        """Rehydrate a persisted ``current_remap`` provenance object."""
        review = raw.get("review_reason")
        return cls(
            source_revision_id=str(raw["source_revision_id"]),
            strategy=RemapStrategy(str(raw["strategy"])),
            confidence=float(raw.get("confidence", 0.0)),
            target_revision_id=raw.get("target_revision_id"),
            matched_block_id=raw.get("matched_block_id"),
            review_reason=ReviewReason(str(review)) if review else None,
            evidence=str(raw.get("evidence", "")),
        )


def fingerprint_text(block_type: str, content: str) -> str:
    """Return the deterministic ``sha256:<hex>`` structural fingerprint of a block's content."""
    canonical = f"{block_type}\x1f{content}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _content_text(content: object) -> str:
    """Flatten a block's ``content`` projection to comparable text (list items joined)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return " ".join(_content_text(item) for item in content)
    return str(content)


def _flatten_snapshot(
    blocks: Sequence[Mapping[str, Any]], ancestry: tuple[str, ...]
) -> list[BlockSnapshot]:
    snapshots: list[BlockSnapshot] = []
    for raw in blocks:
        block_id = str(raw.get("id", ""))
        block_type = str(raw.get("type", ""))
        data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
        content = data.get("content") if isinstance(data, Mapping) else None
        text = _content_text(content)
        snapshots.append(
            BlockSnapshot(
                block_id=block_id,
                text=text,
                fingerprint=fingerprint_text(block_type, text),
                ancestry=ancestry,
            )
        )
        children = raw.get("children") or []
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            snapshots.extend(_flatten_snapshot(children, (*ancestry, block_id)))
    return snapshots


def snapshot_from_document_blocks(
    revision_id: str, blocks: Sequence[Mapping[str, Any]]
) -> RevisionSnapshot:
    """Build a :class:`RevisionSnapshot` from a ``content-document`` block array (pure).

    This is the seam knowledge revisions are projected through for remapping: it reads only the
    published block projection (stable ids + typed content), never knowledge internals.
    """
    return RevisionSnapshot(revision_id=revision_id, blocks=tuple(_flatten_snapshot(blocks, ())))


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _single(candidates: list[BlockSnapshot]) -> BlockSnapshot | None:
    """Return the unique candidate, or ``None`` when there are zero or many (ambiguous)."""
    return candidates[0] if len(candidates) == 1 else None


class Remapper:
    """Pure, deterministic ordered-evidence remapper (FR-ANN-004).

    Stateless and side-effect-free: :meth:`remap` takes the original selector set, the *source*
    revision snapshot (for fingerprint/position evidence) and the *destination* revision snapshot,
    and returns a :class:`RemapResult`. It never mutates its inputs and never guesses — non-unique
    or absent evidence yields a ``NEEDS_REVIEW`` result.
    """

    def remap(
        self,
        *,
        selectors: tuple[Selector, ...],
        source: RevisionSnapshot,
        destination: RevisionSnapshot,
    ) -> RemapResult:
        block_selector = find_block_selector(selectors)
        quote = find_text_quote_selector(selectors)
        position = find_text_position_selector(selectors)
        saw_evidence = False

        # Tier 1 — stable block identity.
        if block_selector is not None:
            match = destination.block(block_selector.block_id)
            if match is not None:
                return self._mapped(
                    RemapStrategy.BLOCK_IDENTITY, source, destination, match, "block id present"
                )

            # Tier 2 — the original block id survives as an ancestor of exactly one new block.
            ancestry_candidates = [
                block for block in destination.blocks if block_selector.block_id in block.ancestry
            ]
            if ancestry_candidates:
                saw_evidence = True
                unique = _single(ancestry_candidates)
                if unique is not None:
                    return self._mapped(
                        RemapStrategy.BLOCK_ANCESTRY,
                        source,
                        destination,
                        unique,
                        "descendant of original block",
                    )

            # Tier 3 — structural/content fingerprint of the original block.
            source_block = source.block(block_selector.block_id)
            if source_block is not None:
                fingerprint_candidates = [
                    block
                    for block in destination.blocks
                    if block.fingerprint == source_block.fingerprint
                ]
                if fingerprint_candidates:
                    saw_evidence = True
                    unique = _single(fingerprint_candidates)
                    if unique is not None:
                        return self._mapped(
                            RemapStrategy.CONTENT_FINGERPRINT,
                            source,
                            destination,
                            unique,
                            "matching content fingerprint",
                        )

        # Tier 4 — exact quote text.
        if quote is not None:
            exact = _normalize(quote.exact)
            quote_candidates = [
                block for block in destination.blocks if exact in _normalize(block.text)
            ]
            if quote_candidates:
                saw_evidence = True
                unique = _single(quote_candidates)
                if unique is not None:
                    return self._mapped(
                        RemapStrategy.EXACT_QUOTE, source, destination, unique, "exact quote match"
                    )

            # Tier 5 — prefix + exact + suffix context.
            if quote.prefix is not None or quote.suffix is not None:
                needle = _normalize(f"{quote.prefix or ''}{quote.exact}{quote.suffix or ''}")
                prefix_candidates = [
                    block for block in destination.blocks if needle in _normalize(block.text)
                ]
                if prefix_candidates:
                    saw_evidence = True
                    unique = _single(prefix_candidates)
                    if unique is not None:
                        return self._mapped(
                            RemapStrategy.PREFIX_SUFFIX,
                            source,
                            destination,
                            unique,
                            "prefix/suffix match",
                        )

        # Tier 6 — normalized character position across the destination's concatenated text.
        if position is not None:
            located = self._locate_by_position(position.start, position.end, destination)
            if located is not None:
                saw_evidence = True
                return self._mapped(
                    RemapStrategy.NORMALIZED_POSITION,
                    source,
                    destination,
                    located,
                    "normalized position",
                )

        reason = ReviewReason.AMBIGUOUS if saw_evidence else ReviewReason.ORPHANED
        return RemapResult(
            source_revision_id=source.revision_id,
            strategy=RemapStrategy.NEEDS_REVIEW,
            confidence=_CONFIDENCE[RemapStrategy.NEEDS_REVIEW],
            review_reason=reason,
            evidence=(
                "non-unique evidence across all tiers"
                if reason is ReviewReason.AMBIGUOUS
                else "no surviving evidence in the new revision"
            ),
        )

    @staticmethod
    def _mapped(
        strategy: RemapStrategy,
        source: RevisionSnapshot,
        destination: RevisionSnapshot,
        block: BlockSnapshot,
        evidence: str,
    ) -> RemapResult:
        return RemapResult(
            source_revision_id=source.revision_id,
            strategy=strategy,
            confidence=_CONFIDENCE[strategy],
            target_revision_id=destination.revision_id,
            matched_block_id=block.block_id,
            evidence=evidence,
        )

    @staticmethod
    def _locate_by_position(
        start: int, end: int, destination: RevisionSnapshot
    ) -> BlockSnapshot | None:
        """Return the block whose normalized-text span contains [start, end), if unique."""
        cursor = 0
        located: list[BlockSnapshot] = []
        for block in destination.blocks:
            length = len(_normalize(block.text))
            block_start = cursor
            block_end = cursor + length
            if start >= block_start and end <= block_end and length > 0:
                located.append(block)
            cursor = block_end
        return _single(located)
