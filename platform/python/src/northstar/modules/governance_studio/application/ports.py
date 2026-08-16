"""Application ports for the Governance Studio (hexagonal boundary, LAW-12).

The Studio owns no domain tables. It reads other modules' state and evidence only through injected
ports/buses, never by importing their persistence:

* :data:`SurfaceResourceResolver` maps a permission (capability action) to the :class:`ResourceRef`
  the kernel policy engine needs to decide it — supplied by the composition root, which is the one
  place allowed to know cross-module resource shapes.
* :class:`AuditReaderPort` exposes previously recorded audit evidence for the read-only audit
  explorer (FR-CMS-006); the adapter wraps the kernel audit recorder's public trail.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from northstar.kernel.audit.ports import AuditRecord
from northstar.kernel.context import RequestContext, ResourceRef

# Resolves the policy resource for a permission in the caller's context (composition-root supplied).
SurfaceResourceResolver = Callable[[RequestContext, str], ResourceRef | None]


@runtime_checkable
class AuditReaderPort(Protocol):
    """Read-only access to recorded audit evidence for the Studio audit explorer (FR-CMS-006).

    Implementations expose the append-only trail; the Studio never writes audit records itself
    (the command bus does) and never reads another module's tables directly.
    """

    def records(self) -> Sequence[AuditRecord]: ...
