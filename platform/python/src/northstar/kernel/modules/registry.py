"""The kernel module registry: registration, dependency graph and activation ordering.

Responsibilities (single-purpose, LAW-02):
  * hold registered :class:`ModuleManifest` value objects (rejecting duplicate ids);
  * validate the dependency graph deterministically (missing deps, cycles);
  * compute a stable topological activation order.

All failures raise typed errors carrying explainable diagnostics; ordering is deterministic
(ties broken by module_id) so the same input always yields the same plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from ..errors import (
    DependencyCycle,
    Diagnostic,
    DuplicateModule,
    MissingDependency,
    RegistryDiagnostics,
)
from .manifest import ModuleManifest


@dataclass(frozen=True, slots=True)
class ActivationPlan:
    """A deterministic, dependency-first module activation order.

    ``order`` lists module ids such that every module appears after all of its required
    dependencies. It is the authoritative result object returned by
    :meth:`ModuleRegistry.resolve_activation_order`.
    """

    order: tuple[str, ...]

    def __iter__(self) -> Iterator[str]:
        return iter(self.order)

    def __len__(self) -> int:
        return len(self.order)


class ModuleRegistry:
    """Registers module manifests and resolves their activation order.

    Registration is deny-by-default for collisions: a module_id may be registered once
    (:class:`DuplicateModule` otherwise). Dependency resolution is separated from
    registration so a caller can register in any order and then plan.
    """

    def __init__(self) -> None:
        self._manifests: dict[str, ModuleManifest] = {}

    def register(self, manifest: ModuleManifest) -> None:
        """Register one manifest. Raises :class:`DuplicateModule` on id collision."""
        if manifest.module_id in self._manifests:
            raise DuplicateModule(manifest.module_id)
        self._manifests[manifest.module_id] = manifest

    def register_all(self, manifests: Iterable[ModuleManifest]) -> None:
        """Register several manifests; fails atomically-per-item on the first duplicate."""
        for manifest in manifests:
            self.register(manifest)

    def get(self, module_id: str) -> ModuleManifest | None:
        return self._manifests.get(module_id)

    @property
    def module_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._manifests))

    def diagnose(self) -> RegistryDiagnostics:
        """Return every dependency-graph problem without raising (non-raising inspection).

        Empty diagnostics means :meth:`resolve_activation_order` will succeed.
        """
        diagnostics: list[Diagnostic] = []
        diagnostics.extend(self._missing_dependency_diagnostics())
        cycle = self._find_cycle()
        if cycle is not None:
            diagnostics.append(
                Diagnostic(
                    code="DEPENDENCY_CYCLE",
                    message="dependency cycle detected: " + " -> ".join(cycle),
                    detail=" -> ".join(cycle),
                )
            )
        return RegistryDiagnostics(tuple(sorted(diagnostics, key=lambda d: d.sort_key())))

    def resolve_activation_order(self) -> ActivationPlan:
        """Compute the deterministic dependency-first activation order.

        Raises :class:`MissingDependency` when a required dependency is absent, and
        :class:`DependencyCycle` when the graph is not a DAG.
        """
        for module_id in sorted(self._manifests):
            manifest = self._manifests[module_id]
            for dep in manifest.dependencies:
                if dep.optional:
                    continue
                if dep.module_id not in self._manifests:
                    raise MissingDependency(module_id, dep.module_id)

        order = self._topological_order()
        return ActivationPlan(tuple(order))

    def _edges(self) -> Mapping[str, tuple[str, ...]]:
        """Directed edges dependency -> dependents, restricted to registered required deps."""
        edges: dict[str, list[str]] = {mid: [] for mid in self._manifests}
        for module_id in self._manifests:
            for dep in self._manifests[module_id].dependencies:
                if dep.optional or dep.module_id not in self._manifests:
                    continue
                edges[dep.module_id].append(module_id)
        return {k: tuple(sorted(v)) for k, v in edges.items()}

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm with deterministic (sorted) tie-breaking."""
        indegree: dict[str, int] = dict.fromkeys(self._manifests, 0)
        for module_id in self._manifests:
            for dep in self._manifests[module_id].dependencies:
                if dep.optional or dep.module_id not in self._manifests:
                    continue
                indegree[module_id] += 1

        edges = self._edges()
        ready = sorted(mid for mid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            newly_ready: list[str] = []
            for dependent in edges[current]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    newly_ready.append(dependent)
            for node in newly_ready:
                _insort(ready, node)

        if len(order) != len(self._manifests):
            cycle = self._find_cycle()
            raise DependencyCycle(tuple(cycle or ()))
        return order

    def _missing_dependency_diagnostics(self) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        for module_id in sorted(self._manifests):
            for dep in self._manifests[module_id].dependencies:
                if dep.optional or dep.module_id in self._manifests:
                    continue
                diagnostics.append(
                    Diagnostic(
                        code="MISSING_DEPENDENCY",
                        message=(
                            f"module '{module_id}' requires '{dep.module_id}', "
                            "which is not registered"
                        ),
                        module_id=module_id,
                        detail=dep.module_id,
                    )
                )
        return diagnostics

    def _find_cycle(self) -> list[str] | None:
        """Return one cycle (as a closed path) if the required-dependency graph has one."""
        white, gray, black = 0, 1, 2
        color = dict.fromkeys(self._manifests, white)
        stack: list[str] = []

        def visit(node: str) -> list[str] | None:
            color[node] = gray
            stack.append(node)
            for dep in sorted(
                d.module_id
                for d in self._manifests[node].dependencies
                if not d.optional and d.module_id in self._manifests
            ):
                if color[dep] == gray:
                    start = stack.index(dep)
                    return [*stack[start:], dep]
                if color[dep] == white:
                    found = visit(dep)
                    if found is not None:
                        return found
            color[node] = black
            stack.pop()
            return None

        for module_id in sorted(self._manifests):
            if color[module_id] == white:
                found = visit(module_id)
                if found is not None:
                    return found
        return None


def _insort(sorted_list: list[str], value: str) -> None:
    """Insert ``value`` into an already-sorted list, preserving order (stdlib bisect)."""
    lo, hi = 0, len(sorted_list)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_list[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    sorted_list.insert(lo, value)
