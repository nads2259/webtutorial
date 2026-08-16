"""The Data-Subject-Rights registry: the seam owned modules register export/erase handlers on.

This is the crux of deletion propagation (EVAL-DATA-009): owned modules and infrastructure stores
register an :class:`~northstar.modules.privacy.application.ports.ExportHandlerPort` and/or
:class:`~northstar.modules.privacy.application.ports.ErasureHandlerPort`. The privacy erase
capability then fans an erase out across EVERY registered store and verifies the deletion residue
is zero; the export capability gathers every store's data into one portable bundle.

The registry is pure (no infrastructure) and holds no ambient authority — it only composes the
handlers it is explicitly given (rule 20, LAW-15). A store id may register at most one exporter and
one eraser (one authoritative handler per store), so propagation is deterministic and complete.
"""

from __future__ import annotations

from .ports import ErasureHandlerPort, ExportHandlerPort


class DataSubjectRightsRegistry:
    """Holds the registered export/erase handlers keyed by their ``store_id``."""

    def __init__(self) -> None:
        self._exporters: dict[str, ExportHandlerPort] = {}
        self._erasers: dict[str, ErasureHandlerPort] = {}

    def register_exporter(self, handler: ExportHandlerPort) -> None:
        """Register the one authoritative export handler for ``handler.store_id``."""
        store_id = handler.store_id
        if store_id in self._exporters:
            raise ValueError(f"an export handler is already registered for store {store_id!r}")
        self._exporters[store_id] = handler

    def register_eraser(self, handler: ErasureHandlerPort) -> None:
        """Register the one authoritative erasure handler for ``handler.store_id``."""
        store_id = handler.store_id
        if store_id in self._erasers:
            raise ValueError(f"an erasure handler is already registered for store {store_id!r}")
        self._erasers[store_id] = handler

    def register_store(self, handler: object) -> None:
        """Convenience: register a handler that implements both export and erasure seams."""
        if isinstance(handler, ExportHandlerPort):
            self.register_exporter(handler)
        if isinstance(handler, ErasureHandlerPort):
            self.register_eraser(handler)

    def exporters(self) -> tuple[ExportHandlerPort, ...]:
        return tuple(self._exporters[store_id] for store_id in sorted(self._exporters))

    def erasers(self) -> tuple[ErasureHandlerPort, ...]:
        return tuple(self._erasers[store_id] for store_id in sorted(self._erasers))

    def store_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._exporters) | set(self._erasers)))


__all__ = ["DataSubjectRightsRegistry"]
