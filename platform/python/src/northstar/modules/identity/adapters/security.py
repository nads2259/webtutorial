"""Shared adapter-side security helpers (session-token hashing).

The session store persists only the SHA-256 of the opaque session token, never the raw token
(docs/07 §4, rule 50). Both the in-memory and SQLAlchemy session stores hash through this single
function so the digest can never drift between them.
"""

from __future__ import annotations

import hashlib


def hash_session_token(raw_token: str) -> str:
    """Return the hex SHA-256 of an opaque session token (stored in place of the raw token)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
