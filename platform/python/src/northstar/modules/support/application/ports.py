"""Ports (abstractions) for the support application layer (rule 10/20, DIP).

:class:`SupportRepositoryPort` is the module's OWN tenant-scoped persistence for cases, messages,
support-access grants and the access-audit log (LAW-13). It holds no ambient authority (rule 50):
every method is organization-scoped and the SQLAlchemy adapter sets the tenant GUC so PostgreSQL
FORCED RLS applies as defense-in-depth.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.model import SupportAccessGrant, SupportCase, SupportMessage


@runtime_checkable
class SupportRepositoryPort(Protocol):
    """Persists/reads support cases, messages, access grants and the access-audit log."""

    # Cases + messages --------------------------------------------------
    def add_case(self, *, organization_id: str, case: SupportCase) -> None: ...

    def get_case(self, *, organization_id: str, case_id: str) -> SupportCase | None: ...

    def save_case(self, *, organization_id: str, case: SupportCase) -> None: ...

    def add_message(
        self, *, organization_id: str, case_id: str, message: SupportMessage, body: str
    ) -> None: ...

    # Support-access grants (deny-by-default, time-bounded) -------------
    def add_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None: ...

    def get_grant(self, *, organization_id: str, grant_id: str) -> SupportAccessGrant | None: ...

    def save_access_grant(self, *, organization_id: str, grant: SupportAccessGrant) -> None: ...

    def active_grant_for(
        self, *, organization_id: str, case_id: str, staff_id: str, now: datetime
    ) -> SupportAccessGrant | None:
        """Return an ACTIVE (unrevoked, unexpired) grant for ``(case, staff)`` or ``None``."""
        ...

    # Access-audit log (tamper-evident evidence) ------------------------
    def record_access(
        self,
        *,
        organization_id: str,
        log_id: str,
        case_id: str,
        staff_id: str,
        scope: str,
        decision: str,
        now: datetime,
    ) -> None: ...

    def list_access_log(
        self, *, organization_id: str, case_id: str
    ) -> Sequence[dict[str, object]]: ...
