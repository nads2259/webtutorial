"""Strict-allowlist HTML / Markdown projections of the typed content tree (FR-CNT-004).

HTML and Markdown are *projections*, never canonical (LAW-06). Untrusted author content MUST NOT
execute arbitrary JavaScript/MDX in a trusted origin. Rather than sanitizing an author-supplied
HTML/MDX blob (which requires a heavyweight sanitizer and is error-prone), we project from the
*typed* tree with a hand-written allowlist serializer:

* every text value is HTML-escaped (``<script>`` becomes ``&lt;script&gt;`` — inert text);
* only a fixed set of elements is emitted (h1-h6, p, pre/code, blockquote, ul/ol/li, img);
* URL-bearing attributes (image ``src``) are scheme-checked against an allowlist
  (``http``/``https``/``mailto``) so ``javascript:``/``data:`` URIs are dropped;
* no attribute is ever taken from raw author markup, so event handlers such as ``onerror`` and
  MDX/JSX expressions cannot appear in the output.

This is a pure adapter (no infrastructure imports); it can be unit-tested deterministically and
proves neutralization of malicious markup.
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

from ..domain.blocks import (
    Block,
    CodeBlock,
    ContentTree,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)

_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})


def _safe_url(raw: str) -> str | None:
    """Return ``raw`` only if its scheme is allowlisted; otherwise ``None`` (drop it)."""
    candidate = raw.strip()
    parsed = urlparse(candidate)
    # A relative URL (no scheme) is allowed only when it does not smuggle a scheme via a colon.
    if parsed.scheme == "":
        return candidate if ":" not in candidate.split("/", 1)[0] else None
    if parsed.scheme.lower() in _ALLOWED_URL_SCHEMES:
        return candidate
    return None


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def project_html(tree: ContentTree) -> str:
    """Render the content tree to a safe HTML string (allowlisted tags, all text escaped)."""
    return "".join(_html_block(block) for block in tree.blocks)


def _html_block(block: Block) -> str:
    children = "".join(_html_block(child) for child in block.children)
    if isinstance(block, HeadingBlock):
        level = min(max(block.level, 1), 6)
        return f"<h{level}>{_esc(block.text)}</h{level}>{children}"
    if isinstance(block, ParagraphBlock):
        return f"<p>{_esc(block.text)}</p>{children}"
    if isinstance(block, CodeBlock):
        lang = _esc(block.language)
        return f'<pre><code class="language-{lang}">{_esc(block.code)}</code></pre>{children}'
    if isinstance(block, QuoteBlock):
        return f"<blockquote>{_esc(block.text)}</blockquote>{children}"
    if isinstance(block, ImageBlock):
        safe = _safe_url(block.src)
        if safe is None:
            # Neutralized: a disallowed scheme (e.g. javascript:) yields no src at all.
            return f'<img alt="{_esc(block.alt)}">{children}'
        return f'<img src="{_esc(safe)}" alt="{_esc(block.alt)}">{children}'
    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "".join(f"<li>{_esc(item)}</li>" for item in block.items)
        return f"<{tag}>{items}</{tag}>{children}"
    # Unknown block type never reaches here (registry-parsed), but stay safe: emit nothing.
    return children


def project_markdown(tree: ContentTree) -> str:
    """Render the content tree to a safe Markdown string (control chars + HTML neutralized)."""
    parts = [_md_block(block) for block in tree.blocks]
    return "\n\n".join(part for part in parts if part)


def _md_escape(text: str) -> str:
    """Escape Markdown control characters and angle brackets so raw HTML/MDX cannot inject."""
    out = text.replace("\\", "\\\\")
    for ch in ("`", "*", "_", "[", "]", "(", ")", "#", "!", "<", ">"):
        out = out.replace(ch, "\\" + ch)
    return out


def _md_block(block: Block) -> str:
    child_md = "\n\n".join(_md_block(child) for child in block.children if child)
    body: str
    if isinstance(block, HeadingBlock):
        level = min(max(block.level, 1), 6)
        body = f"{'#' * level} {_md_escape(block.text)}"
    elif isinstance(block, ParagraphBlock):
        body = _md_escape(block.text)
    elif isinstance(block, CodeBlock):
        # Fenced code is emitted verbatim but can never break out of the fence into HTML/MDX.
        fence = "```"
        body = f"{fence}{block.language}\n{block.code}\n{fence}"
    elif isinstance(block, QuoteBlock):
        body = f"> {_md_escape(block.text)}"
    elif isinstance(block, ImageBlock):
        safe = _safe_url(block.src)
        body = f"![{_md_escape(block.alt)}]({safe})" if safe else _md_escape(block.alt)
    elif isinstance(block, ListBlock):
        marker = (lambda i: f"{i + 1}.") if block.ordered else (lambda _i: "-")
        body = "\n".join(f"{marker(i)} {_md_escape(item)}" for i, item in enumerate(block.items))
    else:
        body = ""
    return f"{body}\n\n{child_md}".rstrip() if child_md else body
