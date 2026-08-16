"""Pure identity domain: value objects, invariants and stdlib-only PKCE primitives.

Nothing in this package may import infrastructure (SQLAlchemy, FastAPI, provider SDKs) — it is
enforced by ``scripts/check_architecture.py`` (LAW-12, rule 10). Only the standard library and
the kernel's typed-error base are used here.
"""

from __future__ import annotations
