"""Operational-readiness adapters: latency/SLO math, drain state, streaming cancellation, backup.

These are infra-light, unit-testable building blocks for the operational-readiness evaluations
(GATE-PERFORMANCE / GATE-OPERATIONS): the reference latency harness (EVAL-PERF-001), long-work
streaming cancellation (EVAL-PERF-003), the encrypted backup + PITR drill (EVAL-DATA-004), the
graceful-drain state machine (EVAL-OPS-002) and the per-profile SLO / error-budget evaluator
(NFR-OPS-006). Pure logic is kept free of database/HTTP concerns so it stays deterministic.
"""

from __future__ import annotations

from .backup import Backup, BackupManifest, EncryptedBackupCodec, content_hash
from .drain import (
    AdmissionDecision,
    DrainController,
    DrainRejectedError,
    DrainState,
    MigrationCompatibility,
    check_migration_compatibility,
)
from .latency import (
    READ_P95_BUDGET_MS,
    WRITE_P95_BUDGET_MS,
    LatencyMeasurement,
    measure,
    percentile,
    summarize,
)
from .slo import (
    ErrorBudgetReport,
    LatencyObjective,
    MetricsWindow,
    SloProfile,
    evaluate_error_budget,
    reference_slo_profiles,
)
from .streaming import LongRunningStream, ResourceLedger, StreamProgress

__all__ = [
    "READ_P95_BUDGET_MS",
    "WRITE_P95_BUDGET_MS",
    "AdmissionDecision",
    "Backup",
    "BackupManifest",
    "DrainController",
    "DrainRejectedError",
    "DrainState",
    "EncryptedBackupCodec",
    "ErrorBudgetReport",
    "LatencyMeasurement",
    "LatencyObjective",
    "LongRunningStream",
    "MetricsWindow",
    "MigrationCompatibility",
    "ResourceLedger",
    "SloProfile",
    "StreamProgress",
    "check_migration_compatibility",
    "content_hash",
    "evaluate_error_budget",
    "measure",
    "percentile",
    "reference_slo_profiles",
    "summarize",
]
