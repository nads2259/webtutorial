"""Ports for the entitlement application layer (rule 10/20).

``EntitlementRepositoryPort`` persists and reads grants (scoped by subject/organization).
``EntitlementPort`` is the authoritative decision interface other modules depend on: they ask for
an entitlement *decision*, never for a plan/payment name (ARCH-019, docs/07 §8).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from northstar.kernel.context import ResourceRef

from ..domain.model import EntitlementDecision, EntitlementGrant


@runtime_checkable
class EntitlementRepositoryPort(Protocol):
    """Persists entitlement grants and lists the grants held by a subject."""

    def add_grant(self, grant: EntitlementGrant) -> None: ...

    def list_grants_for_subject(self, subject_id: str) -> Sequence[EntitlementGrant]: ...


@runtime_checkable
class EntitlementPort(Protocol):
    """Answers entitlement decisions for a subject/action/resource (docs/07 §8)."""

    def decide(
        self,
        *,
        subject_id: str,
        action: str,
        resource: ResourceRef,
        now: datetime | None = None,
    ) -> EntitlementDecision: ...
