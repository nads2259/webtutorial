"""Typed, pure simulation-domain errors (LAW-02, rule 30/40).

Deny-by-default, explainable refusals with machine-comparable ``code`` values. Adapters map these
to RFC 9457 problem details at the API edge; the domain stays infrastructure-free. Several of these
are the load-bearing security invariants of the module (rule 50): a forged/expired/over-broad lease,
a non-allowlisted egress attempt, a sandbox-escape attempt and an AI attempt to read a hidden
scoring key all fail CLOSED with one of these typed refusals.
"""

from __future__ import annotations


class SimulationError(Exception):
    """Base class for simulation-domain errors (deny-by-default, explainable)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class SimulationInvariantViolation(SimulationError):  # noqa: N818 canonical error name
    """A pure simulation value-object invariant was violated."""

    def __init__(self, message: str, *, code: str = "simulation.invariant") -> None:
        super().__init__(message, code=code)


class ImmutableDefinitionError(SimulationError):
    """A published simulation definition is immutable; a change is a new version (FR-SIM-001)."""

    def __init__(self, simulation_id: str, version: str) -> None:
        self.simulation_id = simulation_id
        self.version = version
        super().__init__(
            f"published simulation '{simulation_id}' version '{version}' is immutable",
            code="simulation.definition.immutable",
        )


class SimulationNotFound(SimulationError):  # noqa: N818 canonical error name
    """A definition/run is absent or belongs to another tenant (fail closed)."""

    def __init__(self, kind: str, resource_id: str) -> None:
        self.kind = kind
        self.resource_id = resource_id
        super().__init__(
            f"{kind} '{resource_id}' is not available in this scope",
            code=f"simulation.{kind}.not_found",
        )


class LeaseInvalid(SimulationError):  # noqa: N818 canonical error name
    """A lease was rejected: forged signature, expired, or over-broad scope (FR-SIM-004).

    ``reason`` is a machine-comparable discriminator (``forged`` / ``expired`` / ``over_broad`` /
    ``scope_mismatch``); the executor runs NOTHING when a lease fails validation.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(detail, code=f"simulation.lease.{reason}")


class EgressDenied(SimulationError):  # noqa: N818 canonical error name
    """A non-allowlisted network destination was refused (deny-by-default, FR-SIM-003, SSRF)."""

    def __init__(self, destination: str) -> None:
        self.destination = destination
        super().__init__(
            f"egress to '{destination}' is not on the runtime allowlist and is refused",
            code="simulation.egress.denied",
        )


class SandboxEscape(SimulationError):  # noqa: N818 canonical error name
    """A sandbox-escape attempt (secret read / host access / scope break) failed closed."""

    def __init__(self, attempt: str) -> None:
        self.attempt = attempt
        super().__init__(
            f"sandbox escape attempt '{attempt}' was blocked (fail closed)",
            code="simulation.sandbox.escape_blocked",
        )


class QuotaExceeded(SimulationError):  # noqa: N818 canonical error name
    """A run exceeded a CPU/memory/time/step/output quota and was terminated (FR-SIM-003)."""

    def __init__(self, quota: str, limit: int, used: int) -> None:
        self.quota = quota
        self.limit = limit
        self.used = used
        super().__init__(
            f"quota '{quota}' exceeded: used {used} > limit {limit}; run terminated",
            code="simulation.quota.exceeded",
        )


class EvidenceTampered(SimulationError):  # noqa: N818 canonical error name
    """Recorded run evidence failed hash-chain verification (FR-SIM-005, EVAL-SIM-003)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"run evidence for '{run_id}' failed hash-chain verification (tamper detected)",
            code="simulation.evidence.tampered",
        )


class TrustTierNotApproved(SimulationError):  # noqa: N818 canonical error name
    """The requested runtime trust tier is not approved for this tenant (FR-SIM-008)."""

    def __init__(self, tier: str) -> None:
        self.tier = tier
        super().__init__(
            f"runtime trust tier '{tier}' is not approved for this tenant",
            code="simulation.tier.not_approved",
        )


class IsolationDowngrade(SimulationError):  # noqa: N818 canonical error name
    """A lease/policy tier provides weaker isolation than the definition requires (docs/15 §3)."""

    def __init__(self, required: str, offered: str) -> None:
        self.required = required
        self.offered = offered
        super().__init__(
            f"runtime tier '{offered}' cannot downgrade the isolation required by '{required}'",
            code="simulation.tier.downgrade",
        )


class TenantScopeMissing(SimulationError):  # noqa: N818 canonical error name
    """The authenticated request carried no tenant scope (rule 50, deny-by-default)."""

    def __init__(self) -> None:
        super().__init__(
            "tenant scope is required and must come from the authenticated context",
            code="simulation.tenant.missing",
        )
