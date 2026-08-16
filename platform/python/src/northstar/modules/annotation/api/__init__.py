"""Annotation HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    AnnotationApiDependencies,
    bind_annotation_dependencies,
    create_annotation_router,
)

__all__ = [
    "AnnotationApiDependencies",
    "bind_annotation_dependencies",
    "create_annotation_router",
]
