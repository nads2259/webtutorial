"""Northstar AI module: governed model gateway, prompt registry, Tool Broker, RAG and memory.

AI is a scoped actor, never a superuser (LAW-09, ARCH-009): it reaches framework capabilities only
through the Tool Broker, never a database, table or secret directly. See ``module.yaml`` for the
declared capabilities and data ownership, and :mod:`.application.capabilities` for the authoritative
implementations run through the kernel buses.
"""

from __future__ import annotations
