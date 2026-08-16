"""Media module contracts (LAW-11).

The media capability projects onto canonical, registered contracts rather than defining new
divergent shapes: an asset's accessible alternatives use the ``media_time`` selector capability of
``content-block.schema.json`` and the ``MediaFragmentSelector`` shape already used by the annotation
module, and a refused upload surfaces as the RFC 9457 problem produced by
:func:`northstar.adapters.upload.upload_rejected_problem`. This package is the seam where any future
media-specific canonical schema (registered under ``spec/contracts``) would be bound; it introduces
no new untyped payloads here.
"""

from __future__ import annotations

# The canonical selector-capability identifier for time-based media (content-block.schema.json).
MEDIA_TIME_SELECTOR_CAPABILITY = "media_time"

__all__ = ["MEDIA_TIME_SELECTOR_CAPABILITY"]
