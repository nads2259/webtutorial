"""Curriculum product: import a prepared markdown curriculum into the knowledge DB.

This package is a *product-level composition* (like :mod:`northstar.products.reference`): it forks
no kernel/module source and adds no capability. It parses prepared lesson markdown into the typed
``knowledge`` block model and seeds it ENTIRELY through released capabilities (create/submit/publish
+ taxonomy + retrieval index) dispatched on the composed command bus.
"""

from __future__ import annotations

from .parser import LessonDoc, parse_lesson

__all__ = ["LessonDoc", "parse_lesson"]
