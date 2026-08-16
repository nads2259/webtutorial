"""In-process capability-dispatch guard honouring enabled/disabled lifecycle state (FR-EXT-005).

An extension's capabilities/hooks reach the platform ONLY through this guard, which consults the
authoritative registry state before dispatching. After ``extension.disable`` or
``extension.uninstall`` the extension is no longer enabled, so the guard refuses to dispatch any of
its hooks and its granted actions no longer execute — a disabled/uninstalled extension cannot run
(EVAL-EXT-006, the module's lifecycle-stop invariant). This is the reference in-process isolation
seam; a T1/T2 deployment swaps in an out-of-process broker behind the same contract.
"""

from __future__ import annotations

from collections.abc import Callable

from ..application.ports import ExtensionRegistryPort
from ..domain.errors import ExtensionDisabled, ExtensionNotFound
from ..domain.model import LifecycleState


class CapabilityDispatchGuard:
    """Gates extension hook/capability dispatch on the live enabled/disabled state (fail closed)."""

    def __init__(self, *, registry: ExtensionRegistryPort) -> None:
        self._registry = registry

    def authorize(self, *, organization_id: str, extension_id: str, action: str) -> None:
        """Raise unless ``extension_id`` is installed, ENABLED and granted ``action`` (deny)."""
        record = self._registry.get(organization_id=organization_id, extension_id=extension_id)
        if record is None:
            # Uninstalled (or never installed): its grants are gone — nothing dispatches.
            raise ExtensionNotFound(extension_id)
        if record.state is not LifecycleState.ENABLED:
            raise ExtensionDisabled(extension_id, record.state.value)
        if action not in record.granted_actions:
            # A disabled/uninstalled extension had its grants revoked; ungranted actions are denied.
            raise ExtensionDisabled(extension_id, "revoked")

    def dispatch[T](
        self,
        *,
        organization_id: str,
        extension_id: str,
        action: str,
        handler: Callable[[], T],
    ) -> T:
        """Authorize the extension's lifecycle state + grant, then run ``handler`` (fail closed)."""
        self.authorize(organization_id=organization_id, extension_id=extension_id, action=action)
        return handler()
