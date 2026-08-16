"""Typed kernel domain errors and the explainable diagnostic value object.

Stdlib-only (LAW-02): the kernel raises typed domain errors that carry deterministic,
explainable diagnostics rather than bare strings or dicts. Adapters map these to
RFC 9457 problem details at the trust boundary (rule 30/40); the kernel stays pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single explainable, machine-comparable diagnostic.

    ``code`` is a stable identifier; ``message`` is human-readable; ``module_id`` and
    ``detail`` scope the finding. Instances are ordered deterministically so callers can
    diff them without depending on discovery order.
    """

    code: str
    message: str
    module_id: str | None = None
    detail: str | None = None

    def sort_key(self) -> tuple[str, str, str]:
        return (self.module_id or "", self.code, self.message)


class KernelError(Exception):
    """Base class for all kernel domain errors.

    Carries a tuple of :class:`Diagnostic` so failures are explainable and deterministic.
    """

    def __init__(self, message: str, diagnostics: tuple[Diagnostic, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics: tuple[Diagnostic, ...] = diagnostics

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class ManifestInvalid(KernelError):  # noqa: N818 canonical error name
    """A module manifest failed structural/semantic validation.

    ``issues`` lists every problem found (deterministically sorted) so a caller sees all
    validation failures at once instead of one-at-a-time.
    """

    def __init__(self, message: str, issues: tuple[str, ...] = ()) -> None:
        self.issues: tuple[str, ...] = issues
        diagnostics = tuple(Diagnostic(code="MANIFEST_INVALID", message=issue) for issue in issues)
        super().__init__(message, diagnostics)


class DuplicateModule(KernelError):  # noqa: N818 canonical error name
    """A module_id was registered more than once (registration is idempotent-by-identity)."""

    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        diag = Diagnostic(
            code="DUPLICATE_MODULE",
            message=f"module '{module_id}' is already registered",
            module_id=module_id,
        )
        super().__init__(diag.message, (diag,))


class MissingDependency(KernelError):  # noqa: N818 canonical error name
    """A required module dependency is not present in the registry."""

    def __init__(self, module_id: str, dependency_id: str) -> None:
        self.module_id = module_id
        self.dependency_id = dependency_id
        diag = Diagnostic(
            code="MISSING_DEPENDENCY",
            message=(f"module '{module_id}' requires '{dependency_id}', which is not registered"),
            module_id=module_id,
            detail=dependency_id,
        )
        super().__init__(diag.message, (diag,))


class DependencyCycle(KernelError):  # noqa: N818 canonical error name
    """The module dependency graph contains a cycle (it must be a DAG)."""

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        rendered = " -> ".join(cycle)
        diag = Diagnostic(
            code="DEPENDENCY_CYCLE",
            message=f"dependency cycle detected: {rendered}",
            detail=rendered,
        )
        super().__init__(diag.message, (diag,))


class UnknownCapability(KernelError):  # noqa: N818 canonical error name
    """Resolution requested a capability name/version that is not registered."""

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        diag = Diagnostic(
            code="UNKNOWN_CAPABILITY",
            message=f"capability '{name}' version '{version}' is not registered",
            detail=f"{name}@{version}",
        )
        super().__init__(diag.message, (diag,))


class DuplicateCapability(KernelError):  # noqa: N818 canonical error name
    """A capability name/version was registered twice (LAW-04: one authoritative impl)."""

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        diag = Diagnostic(
            code="DUPLICATE_CAPABILITY",
            message=(
                f"capability '{name}' version '{version}' is already registered; "
                "one authoritative implementation is allowed (LAW-04)"
            ),
            detail=f"{name}@{version}",
        )
        super().__init__(diag.message, (diag,))


class PolicyDenied(KernelError):  # noqa: N818 canonical error name
    """Authorization was denied for an action (deny-by-default, rule 50/LAW-08).

    Carries the deciding ``decision_id`` and every deny reason as :class:`Diagnostic`, so the
    denial is explainable and referenceable from the audit trail without leaking a raw dict.
    """

    def __init__(
        self,
        action: str,
        decision_id: str,
        reasons: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.action = action
        self.decision_id = decision_id
        self.reason_codes: tuple[str, ...] = tuple(code for code, _ in reasons)
        diagnostics = tuple(
            Diagnostic(code=code, message=message, detail=action) for code, message in reasons
        ) or (
            Diagnostic(
                code="POLICY_DENIED",
                message=f"action '{action}' denied by policy",
                detail=action,
            ),
        )
        super().__init__(
            f"action '{action}' denied by policy (decision {decision_id})", diagnostics
        )


class ConfigurationKeyMissing(KernelError):  # noqa: N818 canonical error name
    """A required configuration key was absent at a trust boundary."""

    def __init__(self, key: str) -> None:
        self.key = key
        diag = Diagnostic(
            code="CONFIG_KEY_MISSING",
            message=f"required configuration key '{key}' is missing",
            detail=key,
        )
        super().__init__(diag.message, (diag,))


class UnknownConfigurationKey(KernelError):  # noqa: N818 canonical error name
    """A configuration key not declared in the schema was supplied (deny unknown keys, FR-KRN-003).

    Unknown production keys are rejected rather than silently ignored so a typo or a stale key can
    never mask a value that was expected to take effect. ``source`` records which layer supplied it.
    """

    def __init__(self, key: str, source: str) -> None:
        self.key = key
        self.source = source
        diag = Diagnostic(
            code="CONFIG_KEY_UNKNOWN",
            message=f"configuration key '{key}' (from {source}) is not declared in the schema",
            detail=key,
        )
        super().__init__(diag.message, (diag,))


class ConfigurationValueInvalid(KernelError):  # noqa: N818 canonical error name
    """A configuration value violated its declared type or range constraint (FR-KRN-003).

    ``reason`` explains the specific violation (wrong type, out of range, not an allowed choice) so
    the rejection is explainable at the trust boundary rather than a bare parse failure.
    """

    def __init__(self, key: str, source: str, reason: str) -> None:
        self.key = key
        self.source = source
        self.reason = reason
        diag = Diagnostic(
            code="CONFIG_VALUE_INVALID",
            message=f"configuration value for '{key}' (from {source}) is invalid: {reason}",
            detail=key,
        )
        super().__init__(diag.message, (diag,))


@dataclass(frozen=True, slots=True)
class RegistryDiagnostics:
    """A typed, deterministic report of module-graph validation problems.

    Empty ``diagnostics`` means the graph is valid. Used by non-raising inspection paths
    so tooling can surface every problem at once.
    """

    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.diagnostics
