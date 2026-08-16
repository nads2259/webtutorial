"""Pure knowledge domain (LAW-02, rule 10): typed blocks, content tree, revisions.

No infrastructure imports live here (stdlib only). The block union + registry type the content
payloads (closing B-DOMAIN for content, rule 40); the content tree computes a deterministic
``content_hash``; revisions are immutable once published (LAW-07). The same canonical tree is
projected into role views (:mod:`.projections`) and round-tripped through Markdown
(:mod:`.interchange`) — one source of truth, many projections (UX-004).
"""

from __future__ import annotations

from .interchange import (
    LossyNode,
    MarkdownDocument,
    markdown_to_tree,
    tree_to_markdown,
)
from .projections import View, project

__all__ = [
    "LossyNode",
    "MarkdownDocument",
    "View",
    "markdown_to_tree",
    "project",
    "tree_to_markdown",
]
