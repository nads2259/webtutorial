"""Governance module: immutable decision records + a time-bounded control-exception engine.

Closes GATE-GOVERNANCE (EVAL-GOV-001 decision trace, EVAL-GOV-002 exception expiry). Distinct
from ``governance_studio`` (which projects module-contributed surfaces): this module OWNS the
governance decisions/exceptions concern (FR-GOV-001/002) and exposes it via capabilities only
(FR-GOV-003).
"""
