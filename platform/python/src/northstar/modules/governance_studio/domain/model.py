"""Pure domain model for the Governance Studio shell (docs/13, FR-CMS-001/002).

Infrastructure-free value objects (LAW-02, rule 10): the Studio is a *module-composed shell*.
Modules declare :class:`StudioContribution`\\s (navigation items + workbenches + the permissions
they require); the shell composes them into a **versioned** :class:`NavigationModel`. Nothing here
touches a database, HTTP framework or another module — composition is projection over declared
metadata, and the authoritative authorization decision is made by the kernel policy engine at the
application layer, never by these objects.

``danger_level`` and ``required_permissions`` are carried so the shell can present sensitive-action
affordances (docs/13 §10), but they are never the authorization mechanism: hiding a surface is a
usability choice, and invoking its action still fails closed at the capability layer (FR-CMS-002).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from .errors import ContributionInvalid

# The shell's contribution/API contract version (cms-contribution ``compatibility.studio_api``).
# A contribution is hostable when it targets the same MAJOR version (additive-only evolution).
STUDIO_API_VERSION = "1.0.0"


def _major(semver: str) -> int:
    try:
        return int(semver.split(".", 1)[0])
    except (ValueError, IndexError) as exc:  # pragma: no cover - guarded by schema pattern
        raise ContributionInvalid(f"invalid semver {semver!r}") from exc


def is_studio_api_compatible(studio_api: str, *, shell_api: str = STUDIO_API_VERSION) -> bool:
    """True when a contribution's ``studio_api`` shares the shell's MAJOR version (rule 40)."""
    return _major(studio_api) == _major(shell_api)


class DangerLevel(StrEnum):
    """How consequential a workbench's actions are (docs/13 §10 safety patterns)."""

    NORMAL = "normal"
    SENSITIVE = "sensitive"
    DANGEROUS = "dangerous"


@dataclass(frozen=True, slots=True)
class NavNode:
    """A navigation entry pointing at a workbench (cms-contribution ``navigation[]``)."""

    id: str
    label_key: str
    workbench_id: str
    order: int = 0
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class Workbench:
    """A workbench surface and the permissions required to use it (``workbenches[]``).

    ``required_permissions`` are capability/action names authorized by the kernel policy engine;
    the shell only *projects* (shows/hides) a workbench, it never authorizes on its own.
    """

    id: str
    route: str
    component: str
    required_permissions: tuple[str, ...] = ()
    danger_level: DangerLevel = DangerLevel.NORMAL


@dataclass(frozen=True, slots=True)
class Widget:
    """A dashboard/slot widget contribution (``widgets[]``)."""

    id: str
    slot: str
    component: str


@dataclass(frozen=True, slots=True)
class StudioContribution:
    """A single module's declared Studio surfaces (validated cms-contribution 1.0 document)."""

    module_id: str
    studio_api: str
    permissions: tuple[str, ...]
    navigation: tuple[NavNode, ...] = ()
    workbenches: tuple[Workbench, ...] = ()
    widgets: tuple[Widget, ...] = ()


@dataclass(frozen=True, slots=True)
class NavigationModel:
    """The composed, role-projected navigation the shell renders (versioned, FR-CMS-001).

    ``version`` is the shell contribution-API version; ``revision`` is a deterministic digest over
    the projected surface identities, so a client can cache-validate and detect drift without the
    ordering of contributions affecting the result.
    """

    version: str
    nodes: tuple[NavNode, ...]
    workbenches: tuple[Workbench, ...]

    @property
    def revision(self) -> str:
        """A stable digest over the projected node/workbench ids (order-independent)."""
        material = {
            "version": self.version,
            "nodes": sorted(n.id for n in self.nodes),
            "workbenches": sorted(w.id for w in self.workbenches),
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def workbench_ids(self) -> frozenset[str]:
        return frozenset(w.id for w in self.workbenches)


def build_contribution(
    *,
    module_id: str,
    studio_api: str,
    permissions: tuple[str, ...],
    navigation: tuple[NavNode, ...] = (),
    workbenches: tuple[Workbench, ...] = (),
    widgets: tuple[Widget, ...] = (),
) -> StudioContribution:
    """Assemble a :class:`StudioContribution`, enforcing referential integrity (nav→workbench).

    Every navigation node must reference a workbench declared in the same contribution, so the
    shell never renders a dangling link.
    """
    declared = {w.id for w in workbenches}
    dangling = sorted(n.workbench_id for n in navigation if n.workbench_id not in declared)
    if dangling:
        raise ContributionInvalid(
            f"navigation references undeclared workbench(es): {', '.join(dangling)}",
            issues=tuple(f"navigation.workbench_id: {wid} not declared" for wid in dangling),
        )
    return StudioContribution(
        module_id=module_id,
        studio_api=studio_api,
        permissions=permissions,
        navigation=navigation,
        workbenches=workbenches,
        widgets=widgets,
    )
