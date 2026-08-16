"""Northstar simulation module (docs/15, FR-SIM-001..008).

Versioned, immutable simulation definitions (schema-valid); deny-by-default runtime policy (egress
allowlist + CPU/memory/time/step quotas); short-lived SIGNED leases the reference executor validates
WITHOUT broad application credentials; a reference in-process sandbox tier that cannot read secrets,
escape its scoped surface or reach non-allowlisted egress; immutable hash-chained run evidence;
deterministic scoring; runtime trust tiers governed by the Studio; and AI coaching as a SCOPED AI
actor that reuses ``ai.answer`` and CANNOT see hidden scoring keys or privileged channels.

Hexagonal (rule 10): the domain imports no infrastructure; provider/runtime behaviour lives behind
ports in :mod:`.adapters`; there is one authoritative capability per action (LAW-04).
"""
