"""Approver directory adapters (deny-by-default authorization for exceptions, rule 50).

The reference in-memory :class:`InMemoryApproverDirectory` answers whether an actor is an
authorized exception approver in a tenant. A real deployment injects a directory backed by the
organization role model behind the same :class:`ApproverDirectoryPort`.
"""

from __future__ import annotations


class InMemoryApproverDirectory:
    """In-memory approver directory for tests and the reference wiring (tenant-scoped)."""

    def __init__(self) -> None:
        self._approvers: dict[str, set[str]] = {}

    def add(self, *, organization_id: str, actor_id: str) -> None:
        self._approvers.setdefault(organization_id, set()).add(actor_id)

    def is_approver(self, *, organization_id: str, actor_id: str) -> bool:
        return actor_id in self._approvers.get(organization_id, set())
