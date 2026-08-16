"""Contribution registry: collect + validate module Studio contributions (FR-CMS-001).

Each module declares its Studio surfaces as a ``cms-contribution`` 1.0 document. The registry
validates every document against the canonical JSON Schema (injected, so the module stays
decoupled from the spec tree) *and* the shell's ``studio_api`` compatibility, then materialises the
pure :class:`StudioContribution` value object. Validation is deny-by-default: a document that does
not conform is rejected with an explainable :class:`ContributionInvalid`, never silently coerced.
"""

from __future__ import annotations

from collections.abc import Mapping

import jsonschema

from ..domain.errors import ContributionInvalid, IncompatibleContribution
from ..domain.model import (
    STUDIO_API_VERSION,
    DangerLevel,
    NavNode,
    StudioContribution,
    Widget,
    Workbench,
    build_contribution,
    is_studio_api_compatible,
)


class ContributionRegistry:
    """Collects and validates module-declared Studio contributions (module-composed shell)."""

    def __init__(self, *, schema: Mapping[str, object]) -> None:
        self._schema = schema
        self._validator = jsonschema.Draft202012Validator(schema)
        self._contributions: dict[str, StudioContribution] = {}

    def register(self, document: Mapping[str, object]) -> StudioContribution:
        """Validate a ``cms-contribution`` document and store the resulting contribution.

        Raises :class:`ContributionInvalid` when the document violates the schema and
        :class:`IncompatibleContribution` when it targets an unhostable ``studio_api`` major.
        """
        errors = sorted(self._validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        if errors:
            issues = tuple(
                f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
                for err in errors
            )
            module_id = str(document.get("module_id", "<unknown>"))
            raise ContributionInvalid(
                f"contribution for {module_id} failed schema validation", issues=issues
            )

        module_id = str(document["module_id"])
        studio_api = str(document["compatibility"]["studio_api"])  # type: ignore[index]
        if not is_studio_api_compatible(studio_api):
            raise IncompatibleContribution(module_id, studio_api, STUDIO_API_VERSION)

        contribution = build_contribution(
            module_id=module_id,
            studio_api=studio_api,
            permissions=tuple(document.get("permissions", ())),  # type: ignore[arg-type]
            navigation=_navigation(document.get("navigation", ())),
            workbenches=_workbenches(document.get("workbenches", ())),
            widgets=_widgets(document.get("widgets", ())),
        )
        if module_id in self._contributions:
            raise ContributionInvalid(
                f"duplicate contribution for module {module_id}",
                issues=(f"module_id: {module_id} already registered",),
            )
        self._contributions[module_id] = contribution
        return contribution

    def contributions(self) -> tuple[StudioContribution, ...]:
        """All registered contributions, ordered by module id for deterministic composition."""
        return tuple(self._contributions[mid] for mid in sorted(self._contributions))


def _navigation(raw: object) -> tuple[NavNode, ...]:
    items: list[NavNode] = []
    for entry in raw:  # type: ignore[union-attr]
        items.append(
            NavNode(
                id=str(entry["id"]),
                label_key=str(entry["label_key"]),
                workbench_id=str(entry["workbench_id"]),
                order=int(entry.get("order", 0)),
                icon=entry.get("icon"),
            )
        )
    return tuple(items)


def _workbenches(raw: object) -> tuple[Workbench, ...]:
    items: list[Workbench] = []
    for entry in raw:  # type: ignore[union-attr]
        items.append(
            Workbench(
                id=str(entry["id"]),
                route=str(entry["route"]),
                component=str(entry["component"]),
                required_permissions=tuple(entry.get("required_permissions", ())),
                danger_level=DangerLevel(entry.get("danger_level", DangerLevel.NORMAL.value)),
            )
        )
    return tuple(items)


def _widgets(raw: object) -> tuple[Widget, ...]:
    items: list[Widget] = []
    for entry in raw:  # type: ignore[union-attr]
        items.append(
            Widget(id=str(entry["id"]), slot=str(entry["slot"]), component=str(entry["component"]))
        )
    return tuple(items)
