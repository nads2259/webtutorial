"""Northstar Governance Studio module.

A module-composed control plane (docs/13): modules declare Studio contributions; the shell composes
and role/scope-projects them and proxies every action to a registered capability through the bus.
The Studio owns no domain tables (LAW-05, ARCH-005/ARCH-022).
"""
