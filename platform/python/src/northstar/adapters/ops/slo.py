"""Per-profile SLO declarations + a pure, deterministic error-budget-burn evaluator (NFR-OPS-006).

The commercial reference SLO profile mirrors ``spec/docs/18`` §10 (99.9% monthly availability for
authenticated read/write, p95 reads < 300 ms and writes < 700 ms excluding long provider work, RPO
15 min / RTO 4 h). The evaluator is a pure function of a metrics sample: given the objective and a
window of good/bad requests it computes the observed availability, how much of the error budget has
been consumed and the burn rate. Release pacing (``spec/docs/36`` §4) keys off ``exhausted``; no
clock, DB or network is involved so it is fully unit-testable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LatencyObjective:
    """p95 latency ceilings for the ordinary read/write API (ms), excluding third-party work."""

    read_p95_ms: float = 300.0
    write_p95_ms: float = 700.0


@dataclass(frozen=True, slots=True)
class SloProfile:
    """A named service-level objective profile selected by product/deployment (docs/18 §10)."""

    name: str
    availability_objective: float
    latency: LatencyObjective
    rpo_minutes: int
    rto_hours: int

    def __post_init__(self) -> None:
        if not 0.0 < self.availability_objective < 1.0:
            raise ValueError("availability_objective must be a ratio in (0, 1)")
        if self.rpo_minutes < 0 or self.rto_hours < 0:
            raise ValueError("RPO/RTO targets must not be negative")


def reference_slo_profiles() -> tuple[SloProfile, ...]:
    """Return the declared reference SLO profiles (authoritative source: docs/18 §10)."""
    return (
        SloProfile(
            name="commercial-reference",
            availability_objective=0.999,
            latency=LatencyObjective(read_p95_ms=300.0, write_p95_ms=700.0),
            rpo_minutes=15,
            rto_hours=4,
        ),
        SloProfile(
            name="enterprise-reference",
            availability_objective=0.999,
            latency=LatencyObjective(read_p95_ms=300.0, write_p95_ms=700.0),
            rpo_minutes=15,
            rto_hours=1,
        ),
    )


@dataclass(frozen=True, slots=True)
class MetricsWindow:
    """A sample of request outcomes over an SLO window (a ``bad`` request violates the SLI)."""

    total_requests: int
    bad_requests: int

    def __post_init__(self) -> None:
        if self.total_requests <= 0:
            raise ValueError("total_requests must be positive")
        if not 0 <= self.bad_requests <= self.total_requests:
            raise ValueError("bad_requests must be within [0, total_requests]")


@dataclass(frozen=True, slots=True)
class ErrorBudgetReport:
    """Deterministic error-budget accounting for one metrics window against one objective."""

    objective: float
    total_requests: int
    bad_requests: int
    observed_availability: float
    error_budget_ratio: float
    budget_consumed_ratio: float
    budget_remaining_ratio: float
    burn_rate: float
    exhausted: bool


def evaluate_error_budget(window: MetricsWindow, objective: float) -> ErrorBudgetReport:
    """Compute error-budget burn for ``window`` against ``objective`` (pure, deterministic).

    ``error_budget_ratio`` is ``1 - objective`` (the allowed bad-request fraction). The consumed
    ratio is the observed bad fraction divided by that budget; ``burn_rate`` equals the consumed
    ratio for a full window (a burn rate > 1 means the window alone would exhaust the budget). The
    budget is ``exhausted`` once consumption reaches the whole allowance.
    """
    if not 0.0 < objective < 1.0:
        raise ValueError("objective must be a ratio in (0, 1)")
    bad_ratio = window.bad_requests / window.total_requests
    observed_availability = 1.0 - bad_ratio
    error_budget_ratio = 1.0 - objective
    consumed = bad_ratio / error_budget_ratio
    remaining = 1.0 - consumed
    return ErrorBudgetReport(
        objective=objective,
        total_requests=window.total_requests,
        bad_requests=window.bad_requests,
        observed_availability=observed_availability,
        error_budget_ratio=error_budget_ratio,
        budget_consumed_ratio=consumed,
        budget_remaining_ratio=remaining,
        burn_rate=consumed,
        exhausted=consumed >= 1.0,
    )
