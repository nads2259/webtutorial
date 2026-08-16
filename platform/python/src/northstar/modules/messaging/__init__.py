"""Northstar messaging, campaigns and templates module (docs/16, FR-MSG-001..007).

A provider-neutral messaging engine that separates transactional from marketing/campaign
messages (FR-MSG-001), renders versioned + IMMUTABLE templates deterministically (FR-MSG-002),
segments audiences over APPROVED attributes only — never arbitrary DB/query access (FR-MSG-003),
schedules per recipient time zone (FR-MSG-004), ALWAYS honours consent + suppression so a
suppressed / unsubscribed / non-consented recipient is NEVER in a marketing send (FR-MSG-005),
submits to a provider behind a port IDEMPOTENTLY so a re-submitted (campaign, recipient,
idempotency-key) never double-sends (FR-MSG-006), and exposes per-campaign open/click tracking that
is privacy-safe (off) by default (FR-MSG-007).

Hexagonal (rule 10): the domain imports no infrastructure; the provider and persistence live behind
ports in :mod:`.adapters`; there is one authoritative capability per action (LAW-04).
"""
