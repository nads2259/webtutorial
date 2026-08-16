"""Governance Studio adapters: read-only edges (audit trail). No domain-table writes."""

from __future__ import annotations

from .audit_reader import RecorderAuditReader

__all__ = ["RecorderAuditReader"]
