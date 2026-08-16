"""Identity infrastructure adapters (infra allowed here, rule 10).

Concrete implementations of the application ports: a deterministic mock OIDC provider for tests, a
SQLAlchemy-backed identity directory and session store (schema ``northstar_identity``), in-memory
implementations for fast unit tests, and reference federation/SCIM adapters (FR-IDN-006).
"""

from __future__ import annotations
