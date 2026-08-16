"""First-party products assembled purely by COMPOSING released framework modules.

A product is NOT a new kernel or a fork: it declares a profile (theme + configuration +
taxonomy + declared SLOs + seed data) and wires the *released* module composition root
(``northstar.processes.api.wiring``) — the same public capabilities the API/Studio/CLI use
(LAW-04, ARCH-024). Product code never imports another module's internals or writes owned
tables directly (LAW-05/LAW-13); it only speaks to capabilities through the kernel buses.
"""

from __future__ import annotations
