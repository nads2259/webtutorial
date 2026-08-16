"""Kernel security ports + pure security policies (no infrastructure — LAW-02/12, rule 10).

The kernel declares *what* protection the platform needs without importing any provider or SDK:

* :class:`EncryptionPort` — authenticated encryption at rest (AES-256-GCM reference adapter);
* the deny-by-default SSRF/egress policy (:class:`EgressPolicy`) + :class:`EgressGuardPort` — the
  single seam every outbound HTTP call passes (fetchers/webhooks/AI tools), EVAL-SEC-005;
* the anti-automation rate-limit policy (:func:`evaluate`, :class:`RateBudget`) +
  :class:`RateLimiterPort` — layered per-actor/tenant/IP throttling at sensitive entry points,
  EVAL-SEC-008;
* the deny-by-default upload/media validation policy (:class:`UploadPolicy`) + the quarantine
  :class:`ScanPort` — content-based MIME sniffing, size + decompression-bomb caps and SVG/HTML
  active-content refusal for every ingested file, EVAL-SEC-004.

Concrete implementations live under :mod:`northstar.adapters` (and the HTTP adapter) behind these
boundaries so a deployment can swap the crypto provider, DNS resolver or distributed limiter
without touching the kernel or any module (rule 50).
"""

from __future__ import annotations

from .egress import (
    AuthorizedEgress,
    EgressBlocked,
    EgressDecision,
    EgressGuardPort,
    EgressPolicy,
    EgressReason,
    ParsedTarget,
    classify_ip,
    parse_target,
)
from .ports import DecryptionError, EncryptionPort
from .rate_limit import (
    GuardedEntryPoint,
    RateBudget,
    RateLimitDecision,
    RateLimiterPort,
    RateLimitExceeded,
    RateLimitKey,
    WindowState,
    default_budgets,
    evaluate,
)
from .upload import (
    DEFAULT_ACCEPTED_TYPES,
    AcceptedType,
    ScanOutcome,
    ScanPort,
    ScanResult,
    UploadLimits,
    UploadPolicy,
    UploadReason,
    UploadRejected,
    ValidatedUpload,
    contains_active_markup,
    inspect_archive,
    sniff_content_type,
)

__all__ = [
    "DEFAULT_ACCEPTED_TYPES",
    "AcceptedType",
    "AuthorizedEgress",
    "DecryptionError",
    "EgressBlocked",
    "EgressDecision",
    "EgressGuardPort",
    "EgressPolicy",
    "EgressReason",
    "EncryptionPort",
    "GuardedEntryPoint",
    "ParsedTarget",
    "RateBudget",
    "RateLimitDecision",
    "RateLimitExceeded",
    "RateLimitKey",
    "RateLimiterPort",
    "ScanOutcome",
    "ScanPort",
    "ScanResult",
    "UploadLimits",
    "UploadPolicy",
    "UploadReason",
    "UploadRejected",
    "ValidatedUpload",
    "WindowState",
    "classify_ip",
    "contains_active_markup",
    "default_budgets",
    "evaluate",
    "inspect_archive",
    "parse_target",
    "sniff_content_type",
]
