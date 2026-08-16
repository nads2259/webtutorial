"""Enforcement + moderator-directory adapters (reversible enforcement, deny-by-default authz).

Enforcement applies and REVERSES the content-level effect a decision produces. An upheld
removal/hide is restored on a granted appeal (FR-ANN-007). The in-memory gateway tracks content
state for tests; the annotation-backed gateway drives the annotation module's own authoritative
``moderate`` capability (hide/unhide) so there is no cross-module table write (LAW-13). The
moderator directory answers deny-by-default authorization (only a moderator may triage/decide).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from northstar.kernel.context import Actor, ActorType, RequestContext

from ..application.ports import ReportableRef
from ..domain.model import EnforcementKind

# The annotation module represents removal/hide as a reversible ``hide``; restore is ``unhide``.
_APPLY_KIND = "hide"
_RESTORE_KIND = "unhide"


class InMemoryModeratorDirectory:
    """In-memory moderator directory (dependency-injected; deny-by-default when unseeded)."""

    def __init__(self) -> None:
        self._moderators: set[tuple[str, str]] = set()

    def add(self, *, organization_id: str, actor_id: str) -> None:
        self._moderators.add((organization_id, actor_id))

    def is_moderator(self, *, organization_id: str, actor_id: str) -> bool:
        return (organization_id, actor_id) in self._moderators


class InMemoryEnforcementGateway:
    """In-memory enforcement gateway that tracks each content item's visibility state (tests)."""

    def __init__(self) -> None:
        # (organization_id, content_id) -> "active" | "removed" | "hidden"
        self._state: dict[tuple[str, str], str] = {}
        self.applied: list[tuple[str, str, str]] = []
        self.restored: list[tuple[str, str, str]] = []

    def state_of(self, *, organization_id: str, content_id: str) -> str:
        return self._state.get((organization_id, content_id), "active")

    def apply(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
    ) -> str | None:
        new_state = "removed" if kind is EnforcementKind.REMOVE else "hidden"
        self._state[(organization_id, target.content_id)] = new_state
        self.applied.append((organization_id, target.content_id, kind.value))
        return f"enf-{organization_id}-{target.content_id}-{kind.value}"

    def restore(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
        receipt: str | None,
    ) -> None:
        self._state[(organization_id, target.content_id)] = "active"
        self.restored.append((organization_id, target.content_id, kind.value))


@runtime_checkable
class AnnotationModerator(Protocol):
    """The minimal annotation moderation surface this gateway drives (hide/unhide by tenant)."""

    def moderate(
        self, *, organization_id: str, actor_id: str, annotation_id: str, kind: str
    ) -> None: ...


class AnnotationEnforcementGateway:
    """Reversible enforcement over annotation content via the annotation ``moderate`` capability."""

    def __init__(self, *, moderator: AnnotationModerator) -> None:
        self._moderator = moderator

    def apply(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
    ) -> str | None:
        self._moderator.moderate(
            organization_id=organization_id,
            actor_id=actor_id,
            annotation_id=target.content_id,
            kind=_APPLY_KIND,
        )
        return f"annotation:{target.content_id}:{kind.value}"

    def restore(
        self,
        *,
        organization_id: str,
        target: ReportableRef,
        kind: EnforcementKind,
        actor_id: str,
        receipt: str | None,
    ) -> None:
        self._moderator.moderate(
            organization_id=organization_id,
            actor_id=actor_id,
            annotation_id=target.content_id,
            kind=_RESTORE_KIND,
        )


@dataclass(frozen=True, slots=True)
class _ModerateInvocation:
    """A minimal invocation object matching the annotation ``Moderate`` handler contract."""

    context: RequestContext
    payload: object


class AnnotationModerationGateway:
    """Drives the annotation module's authoritative ``Moderate`` capability (no table write)."""

    def __init__(self, *, moderate_handler: object, command_factory: object) -> None:
        self._handler = moderate_handler
        self._command_factory = command_factory

    def moderate(
        self, *, organization_id: str, actor_id: str, annotation_id: str, kind: str
    ) -> None:
        context = RequestContext(
            actor=Actor(type=ActorType.SERVICE, id=actor_id),
            correlation_id=f"moderation-enforcement-{annotation_id}",
            tenant_scope=organization_id,
        )
        command = self._command_factory(annotation_id=annotation_id, kind=kind, reason=None)
        self._handler.handle(_ModerateInvocation(context=context, payload=command))
