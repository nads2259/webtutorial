"""Role-aware view projections of the *same* canonical content tree (UX-004, docs/12 §1).

docs/12: "One object, multiple projections. Learner, author, researcher, reviewer, moderator and
administrator views address the same governed knowledge object." There is a single source of truth
— the typed :class:`~northstar.modules.knowledge.domain.blocks.ContentTree` — and each role view is
a deterministic *projection* of it, never a divergent copy.

Two concrete role projections are provided here:

* ``learner`` — the calm reading experience (docs/12 §5): structural content only, no technical
  block ids and no editorial metadata;
* ``author`` — the editorial experience: the same content plus stable block ids and author-only
  editorial metadata (``editorial_note``).

Both derive from one tree via :func:`project`, so the views cannot drift apart. This module is pure
and stdlib-only (rule 10): it imports no infrastructure.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .blocks import Block, ContentTree
from .errors import KnowledgeInvariantViolation


class View(StrEnum):
    """The role viewpoints a canonical content object can be projected into (docs/12 §1)."""

    LEARNER = "learner"
    AUTHOR = "author"


def project(tree: ContentTree, *, view: View | str) -> dict[str, Any]:
    """Project ``tree`` into the role view model named by ``view``.

    Returns a plain, JSON-serializable view model ``{"view", "blocks": [...]}``. The ``learner``
    view hides technical block ids and editorial metadata; the ``author`` view includes both. Both
    are computed from the same tree (single source of truth), so they never diverge. An unknown
    view name is rejected deny-by-default.
    """
    try:
        resolved = View(view)
    except ValueError as exc:
        raise KnowledgeInvariantViolation(
            f"unknown role view {view!r}", code="knowledge.projection.view"
        ) from exc
    include_ids = resolved is View.AUTHOR
    include_editorial = resolved is View.AUTHOR
    return {
        "view": resolved.value,
        "blocks": [
            _view_block(block, include_ids=include_ids, include_editorial=include_editorial)
            for block in tree.blocks
        ],
    }


def _view_block(block: Block, *, include_ids: bool, include_editorial: bool) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": block.block_type,
        "attributes": block.attributes(),
        "content": block.content(),
    }
    if include_ids:
        node["id"] = block.block_id
    if include_editorial:
        node["editorial_note"] = block.editorial_note
    node["children"] = [
        _view_block(child, include_ids=include_ids, include_editorial=include_editorial)
        for child in block.children
    ]
    return node
