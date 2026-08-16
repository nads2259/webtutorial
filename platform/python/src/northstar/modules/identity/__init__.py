"""Northstar identity module (first-party module over the kernel).

Implements subjects/users, browser authentication via OIDC/OAuth Authorization Code + PKCE with
server-managed secure sessions, MFA/WebAuthn-passkey-ready ports, session rotation/revocation and
federation/SCIM adapter ports (docs/07, FR-IDN-001..006, NFR-SEC-001).

The module follows the hexagonal layout mandated by rule 10: ``domain`` is pure (no infra
imports); ``application`` holds the capabilities and their ports; ``adapters`` provide concrete
infrastructure (a deterministic mock OIDC provider, SQLAlchemy repositories/session store, and
federation/SCIM ports); ``api`` is the thin FastAPI inbound edge. Every authentication/session
mutation is dispatched through the kernel command bus, authorized deny-by-default and audited.
"""

from __future__ import annotations

MODULE_ID = "northstar.identity"
MODULE_VERSION = "0.3.0"
