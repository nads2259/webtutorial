"""Ports (abstractions) for the moderation application layer (rule 10/20, DIP).

The repository is tenant-aware: every read/write is scoped by ``organization_id`` so a caller can
never reach another tenant's cases (rule 50). The reportable-content port is the read-only seam
onto the *reportable* content (annotations/comments) — moderation references that content through
this port and NEVER reaches its tables (LAW-13). The enforcement port applies and REVERSES the
content-level enforcement a decision produces (an upheld removal is restored on a granted appeal).
The moderator-directory port answers deny-by-default authorization: only a moderator (or the
case assignee) may triage/decide.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.model import CaseEvent, EnforcementKind, ModerationCase, ReportableRef


@dataclass(frozen=True, slots=True)
class ReportableContent:
    """The minimal projection of reportable content this module needs (its author + existence)."""

    content_type: str
    content_id: str
    author_id: str


@runtime_checkable
class ModerationRepositoryPort(Protocol):
    """Persists and reads moderation cases + their append-only event trail, tenant-scoped."""

    def add_case(self, case: ModerationCase, event: CaseEvent) -> None: ...

    def get_case(self, *, organization_id: str, case_id: str) -> ModerationCase | None:
        """Return the case only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def update_case(self, case: ModerationCase, event: CaseEvent) -> None:
        """Persist a lifecycle transition and append its tamper-evident event (tenant-scoped)."""
        ...

    def find_open_case_for_target(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ModerationCase | None:
        """Return the open (non-resolved) case for a target so duplicate reports coalesce."""
        ...

    def list_events(self, *, organization_id: str, case_id: str) -> Sequence[CaseEvent]:
        """Return the ordered lifecycle event trail for a case (evidence)."""
        ...


@runtime_checkable
class ReportableContentPort(Protocol):
    """Read-only projection of reportable content (annotation/comment) into this module (LAW-13)."""

    def describe(
        self, *, organization_id: str, content_type: str, content_id: str
    ) -> ReportableContent | None: ...


@runtime_checkable
class EnforcementPort(Protocol):
    """Applies + REVERSES the content-level enforcement a decision produces (FR-ANN-007).

    ``apply`` returns an opaque receipt recorded on the case; ``restore`` reverses a previously
    applied removal/hide when an appeal is granted, using that receipt.
    """

    def apply(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
    ) -> str | None: ...

    def restore(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
        receipt: str | None,
    ) -> None: ...


@runtime_checkable
class ModeratorDirectoryPort(Protocol):
    """Answers whether an actor is a moderator in a tenant (deny-by-default authorization)."""

    def is_moderator(self, *, organization_id: str, actor_id: str) -> bool: ...
