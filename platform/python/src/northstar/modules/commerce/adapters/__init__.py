"""Commerce adapters: persistence, webhook verifier and the entitlement-engine gateway (rule 10)."""

from __future__ import annotations

from .entitlement_gateway import (
    EntitlementEngineGateway,
    InMemoryCommerceEntitlementRepository,
    SqlAlchemyCommerceEntitlementRepository,
)
from .repositories import InMemoryCommerceRepository, SqlAlchemyCommerceRepository
from .tables import (
    COMMERCE_SCHEMA,
    COMMERCE_TENANT_TABLES,
    CommerceTables,
    build_commerce_tables,
)
from .webhook_verifier import HmacWebhookVerifier, sign_callback

__all__ = [
    "COMMERCE_SCHEMA",
    "COMMERCE_TENANT_TABLES",
    "CommerceTables",
    "EntitlementEngineGateway",
    "HmacWebhookVerifier",
    "InMemoryCommerceEntitlementRepository",
    "InMemoryCommerceRepository",
    "SqlAlchemyCommerceEntitlementRepository",
    "SqlAlchemyCommerceRepository",
    "build_commerce_tables",
    "sign_callback",
]
